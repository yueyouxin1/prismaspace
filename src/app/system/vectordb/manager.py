# src/app/system/vector/manager.py

import logging
import re
from typing import List, Set
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ServiceModule, ServiceModuleVersion, ServiceModuleType
from app.engine.vector.main import VectorEngineManager
from app.services.module.types.specifications import EmbeddingAttributes
from app.services.exceptions import ConfigurationError
from .constants import (
    PREFIX_VECTOR_DB,
    MANAGED_PREFIXES,
    RESERVED_COLLECTIONS,
    DEFAULT_ENGINE_ALIAS
)

logger = logging.getLogger(__name__)

def resolve_collection_name(provider_name: str, model_name: str, dimensions: int) -> str:
    safe_provider = re.sub(r'[^a-zA-Z0-9]', '_', provider_name).lower()
    safe_model = re.sub(r'[^a-zA-Z0-9]', '_', model_name).lower()
    return f"{PREFIX_VECTOR_DB}{safe_provider}_{safe_model}_{dimensions}"

def resolve_collection_name_for_version(version: ServiceModuleVersion):
    attrs = EmbeddingAttributes.model_validate(version.attributes)
    dims = attrs.dimensions
    provider_name = version.service_module.provider.name
    return resolve_collection_name(provider_name, version.name, dims)

class SystemVectorManager:
    def __init__(self, db: AsyncSession, vector_manager: VectorEngineManager):
        self.db = db
        self.vector_manager = vector_manager

    async def _get_embedding_versions(self) -> List[ServiceModuleVersion]:
            type_id_subquery = (
                select(ServiceModuleType.id)
                .where(ServiceModuleType.name == 'embedding')
                .scalar_subquery()
            )

            stmt = (
                select(ServiceModuleVersion)
                .join(ServiceModuleVersion.service_module)
                .where(ServiceModuleVersion.service_module.has(type_id=type_id_subquery))
                .options(joinedload(ServiceModuleVersion.service_module).joinedload(ServiceModule.provider))
            )
            result = await self.db.execute(stmt)
            return result.scalars().unique().all()

    async def _resolve_default_embedding_dims(self) -> int:
        """
        [Strict] 获取系统默认 Embedding 模型的维度。
        如果未配置默认模型，视为严重系统错误，抛出异常。
        """
        stmt = (
            select(ServiceModuleType)
            .where(ServiceModuleType.name == 'embedding')
            .options(joinedload(ServiceModuleType.default_version))
        )
        result = await self.db.execute(stmt)
        emb_type = result.scalars().first()

        if not emb_type:
            raise ConfigurationError("Critical: 'embedding' service module type definition is missing in DB.")
        
        if not emb_type.default_version:
            raise ConfigurationError(
                "Critical: No default embedding module version configured. "
                "System reserved collections cannot be initialized."
            )

        try:
            attrs = EmbeddingAttributes.model_validate(emb_type.default_version.attributes)
            return attrs.dimensions
        except Exception as e:
            raise ConfigurationError(
                f"Critical: Default embedding module has invalid attributes: {e}"
            )

    async def initialize_system_collections(self):
        """
        [Seeding/Startup] 初始化所有标准和预留集合。
        任何一步失败都将抛出异常并中断启动流程。
        """
        logger.info("[SystemVector] Initializing physical vector collections...")
        
        # 1. Standard Collections (Based on Modules)
        versions = await self._get_embedding_versions()
        for version in versions:
            # 单个模块创建失败记录日志但不阻断整体（可选，视严格程度而定，这里保持 ensure 逻辑）
            # 但 ensure 内部如果连接引擎失败还是会抛出的
            await self.ensure_collection_for_version(version)

        # 2. Reserved Collections (Strict)
        # 这一步是系统基石，必须成功
        await self._ensure_reserved_collections()
        
        logger.info("[SystemVector] Initialization complete.")

    async def ensure_collection_for_version(self, version: ServiceModuleVersion):
        try:
            attrs = EmbeddingAttributes.model_validate(version.attributes)
            dims = attrs.dimensions
            provider_name = version.service_module.provider.name
            collection_name = resolve_collection_name(provider_name, version.name, dims)
            
            engine = await self.vector_manager.get_engine(DEFAULT_ENGINE_ALIAS)
            await engine.create_collection(collection_name, vector_size=dims)
            # logger.debug(f"[SystemVector] Ensured collection '{collection_name}'")
        except Exception as e:
            logger.error(f"[SystemVector] Failed to ensure collection for version {version.name}: {e}")
            raise # Re-raise to alert upper layers if needed, or suppress if strictness allows

    async def _ensure_reserved_collections(self):
        """
        [Strict] 批量创建预留集合。
        """
        engine = await self.vector_manager.get_engine(DEFAULT_ENGINE_ALIAS)
        
        # 1. 获取基准维度
        # 目前所有预留集合（如 Agent Memory）都默认基于默认嵌入模型
        # 如果未来有特定集合需要特定模型，需在此处扩展逻辑
        default_dims = await self._resolve_default_embedding_dims()

        # 2. 严格创建流程
        for coll_name in RESERVED_COLLECTIONS:
            logger.info(f"[SystemVector] Ensuring reserved collection: {coll_name} (dim={default_dims})")
            # 引擎层的 create_collection 必须是幂等的（已存在则跳过）
            # 如果失败（如连接超时、权限拒绝），直接抛出异常，不要 catch
            await engine.create_collection(coll_name, vector_size=default_dims)

    async def drop_collection_for_version(self, version: ServiceModuleVersion):
        if version.service_module.type.name != 'embedding': return
        try:
            c_name = resolve_collection_name_for_version(version)
            engine = await self.vector_manager.get_engine(DEFAULT_ENGINE_ALIAS)
            await engine.delete_collection(c_name)
            logger.warning(f"[SystemVector] Dropped collection '{c_name}'")
        except Exception as e:
            logger.error(f"Failed to drop collection: {e}")

    async def prune_orphan_collections(self):
            """
            [Reconcile - Strict Mode] 
            校准物理层：采用严格白名单策略。
            
            策略：
            1. 假定当前向量数据库实例为本项目独占。
            2. 任何不在 (RESERVED_COLLECTIONS + Active Embedding Modules) 中的集合，
            无论名称为何，均视为垃圾或外部干扰，直接物理删除。
            """
            logger.info("[SystemVector] Starting STRICT collection calibration...")
            engine = await self.vector_manager.get_engine(DEFAULT_ENGINE_ALIAS)
            
            # 1. 获取物理层所有集合
            physical_colls = await engine.list_collections()
            if not physical_colls:
                logger.info("[SystemVector] Vector DB is empty. Nothing to prune.")
                return

            # 2. 构建绝对白名单 (The "Keep List")
            # 包含：代码中硬编码的预留集合
            whitelist = set(RESERVED_COLLECTIONS)  
            
            # 包含：当前数据库中活跃的 Embedding Modules 对应的集合
            versions = await self._get_embedding_versions()
            for v in versions:
                try:
                    c_name = resolve_collection_name_for_version(v)
                    whitelist.add(c_name)
                except Exception as e:
                    # 如果元数据解析失败，说明这个 Module 配置坏了。
                    # 此时对应的集合如果存在，也不在白名单内，会被视为垃圾删掉。
                    # 这通常是预期的，因为坏配置不应占用资源。
                    logger.warning(f"[SystemVector] Skipping invalid module version {v.id} during whitelist build: {e}")

            # 3. 识别所有异物 (Identify Aliens)
            # 只要不在白名单里，全是异物
            aliens = [c for c in physical_colls if c not in whitelist]

            if not aliens:
                logger.info(f"[SystemVector] Calibration complete. Environment is clean. ({len(whitelist)} active collections)")
                return

            # 4. 执行清洗 (Purge)
            logger.warning(f"[SystemVector] Found {len(aliens)} unauthorized collections. Purging...")
            for alien in aliens:
                logger.warning(f"[SystemVector] DELETING unauthorized collection: {alien}")
                try:
                    await engine.delete_collection(alien)
                except Exception as e:
                    logger.error(f"[SystemVector] Failed to delete {alien}: {e}")