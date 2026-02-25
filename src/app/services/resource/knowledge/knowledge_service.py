# src/app/services/resource/knowledge_service.py

import uuid
import asyncio
import logging
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, Optional, List, Set, Tuple
from datetime import timedelta
from sqlalchemy import select, func, delete, insert, update, text, Integer
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AppContext
from app.models import User, Workspace, Resource, VersionStatus, DocumentProcessingStatus
from app.models.resource.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeChunk, ChunkProcessingStatus, KnowledgeBaseVersionDocuments
from app.models.module import ServiceModuleVersion
from app.schemas.resource.knowledge.knowledge_schemas import (
    KnowledgeBaseRead, KnowledgeBaseUpdate, KnowledgeBaseExecutionRequest, KnowledgeBaseExecutionResponse, 
    SearchResultChunk, GroupedSearchResult, DocumentRead, BatchChunkUpdate, KnowledgeBaseInstanceConfig, PaginatedDocumentsResponse, 
    DocumentTaskProgress
)
from app.services.resource.base.base_impl_service import register_service, ResourceImplementationService, ValidationResult, DependencyInfo
from app.services.module.service_module_service import ServiceModuleService
from app.services.module.embedding_service import EmbeddingService
from app.services.module.types.specifications import EmbeddingAttributes
from app.services.exceptions import ServiceException, NotFoundError, ConfigurationError
from app.dao.resource.knowledge.knowledge_dao import KnowledgeBaseDao, KnowledgeDocumentDao, KnowledgeChunkDao
from app.dao.module.service_module_dao import ServiceModuleVersionDao

from app.engine.parsing.main import ProcessingPipeline
from app.engine.parsing.base import BasePolicy, ParserPolicy, ChunkerPolicy
from app.engine.vector.base import VectorChunk, VectorEngineError
from app.engine.model.embedding import BatchEmbeddingResult
from app.engine.model.llm import LLMTool
from app.core.trace_manager import TraceManager
from app.services.auditing.types.attributes import KnowledgeBaseAttributes, KnowledgeBaseMeta
from app.system.vectordb.constants import DEFAULT_ENGINE_ALIAS
from app.system.vectordb.manager import resolve_collection_name_for_version

logger = logging.getLogger(__name__)

VECTOR_DB_UPSERT_BATCH_SIZE = 100
MANIFEST_CACHE_TTL_SECONDS = 3600  # 1 hour

@register_service
class KnowledgeBaseService(ResourceImplementationService):
    name: str = "knowledge"

    _TASK_STATUS_KEY_PREFIX = "knowledge:task:status:"
    _TASK_PROGRESS_CHANNEL = "knowledge:task:progress"
    
    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = KnowledgeBaseDao(context.db)
        self.redis = context.redis_service
        self.document_dao = KnowledgeDocumentDao(context.db)
        self.chunk_dao = KnowledgeChunkDao(context.db)
        self.smv_dao = ServiceModuleVersionDao(context.db)
        self.processing_pipeline = ProcessingPipeline()
        self.embedding_service = EmbeddingService(context)

    # ==========================================================================
    # 0. Infrastructure Helpers (Routing & Naming)
    # ==========================================================================

    def _get_manifest_cache_key(self, instance_uuid: str) -> str:
        """Helper to generate a consistent cache key for a version's manifest."""
        return f"knowledge:manifest:{instance_uuid}"

    async def _invalidate_manifest_cache(self, instance_uuid: str):
        """Atomically deletes the manifest cache for a given version."""
        cache_key = self._get_manifest_cache_key(instance_uuid)
        await self.redis.delete_key(cache_key)
        logger.info(f"Invalidated manifest cache for KnowledgeBase instance: {instance_uuid}")

    async def _resolve_physical_collection_name(self, module_version_id: int) -> str:
        """
        [Architecture Core] Dynamically resolves the shared physical collection name.
        Format: sys_vec_{provider}_{model_name}_{dimensions}
        """
        # Load version with attributes
        version = await self.smv_dao.get_by_pk(module_version_id)
        if not version:
            raise ConfigurationError(f"Embedding module version {module_version_id} not found.")
        
        try:
            return resolve_collection_name_for_version(version)
        except Exception as e:
            raise ConfigurationError(f"Invalid embedding module attributes for {version.name}: {e}")

    # ==========================================================================
    # 1. CRUD & Lifecycle (Logical Management)
    # ==========================================================================

    async def serialize_instance(self, instance: KnowledgeBase) -> Dict[str, Any]:
        # [PERFORMANCE] Get document count with an efficient query
        doc_count_query = select(func.count()).select_from(KnowledgeBaseVersionDocuments).where(KnowledgeBaseVersionDocuments.version_id == instance.version_id)
        doc_count = await self.db.scalar(doc_count_query)
        config_obj = KnowledgeBaseInstanceConfig.model_validate(instance.config or {})
        instance_dump = KnowledgeBaseRead(
            uuid=instance.uuid,
            name=instance.name,
            version_tag=instance.version_tag,
            status=instance.status, 
            config=config_obj,
            document_count=doc_count
        ).model_dump()
        return instance_dump

    async def get_by_uuid(self, instance_uuid: str) -> Optional[KnowledgeBase]:
        return await self.dao.get_by_uuid(instance_uuid)

    async def create_instance(self, resource: Resource, actor: User) -> KnowledgeBase:
        """
        Creates a logical KnowledgeBase instance. 
        [Refactor]: No physical collection creation here. Just metadata.
        """
        default_embedding_module = await self.smv_dao.get_default_version_by_type("embedding")
        if not default_embedding_module:
            raise ConfigurationError("No default embedding model configured.")

        default_config = KnowledgeBaseInstanceConfig()
        instance = KnowledgeBase(
            version_tag="__workspace__", 
            status=VersionStatus.WORKSPACE, 
            creator_id=actor.id,
            resource_type="knowledge", 
            name=resource.name,
            # [Refactor]: These fields are kept in model but managed dynamically in logic
            # We can store a placeholder or the intended shared name if helpful for debugging,
            # but the source of truth is _resolve_physical_collection_name
            collection_name="",
            engine_alias=DEFAULT_ENGINE_ALIAS, 
            embedding_module_version_id=default_embedding_module.id,
            config=default_config.model_dump(mode='json'), 
            resource=resource
        )
        return instance

    async def publish_instance(self, workspace_instance: KnowledgeBase, version_tag: str, version_notes: Optional[str], actor: User) -> KnowledgeBase:
        """
        Creates a metadata snapshot. The underlying vectors are shared/isolated via logic.
        """
        async with self.db.begin_nested():
            snapshot = KnowledgeBase(
                resource_id=workspace_instance.resource_id, 
                status=VersionStatus.PUBLISHED,
                version_tag=version_tag, 
                version_notes=version_notes, 
                creator_id=actor.id,
                published_at=func.now(), 
                name=workspace_instance.name,
                collection_name=workspace_instance.collection_name, # Copied but ignored
                engine_alias=workspace_instance.engine_alias,
                embedding_module_version_id=workspace_instance.embedding_module_version_id,
                config=workspace_instance.config.copy() if workspace_instance.config else {}
            )
            self.db.add(snapshot)
            await self.db.flush()

            # Copy document associations
            insert_stmt = insert(KnowledgeBaseVersionDocuments).from_select(
                ['version_id', 'document_id'],
                select(func.cast(snapshot.version_id, Integer), KnowledgeBaseVersionDocuments.document_id)
                .select_from(KnowledgeBaseVersionDocuments)
                .where(KnowledgeBaseVersionDocuments.version_id == workspace_instance.version_id)
            )
            await self.db.execute(insert_stmt)
            
        return snapshot

    async def update_instance(self, instance: KnowledgeBase, update_data: Dict[str, Any]) -> KnowledgeBase:
        validated_data = KnowledgeBaseUpdate.model_validate(update_data)
        update_dict = validated_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(instance, key, value)
        return instance

    async def delete_instance(self, instance: KnowledgeBase) -> None:
        """
        Deletes the logical instance.
        [Refactor]: Physical data cleanup is handled by GC, not here.
        """
        doc_ids_query = select(KnowledgeBaseVersionDocuments.document_id).where(KnowledgeBaseVersionDocuments.version_id == instance.version_id)
        doc_ids_result = await self.db.execute(doc_ids_query)
        doc_ids_to_check = doc_ids_result.scalars().all()

        engine_alias = instance.engine_alias
        embedding_module_id = instance.embedding_module_version_id

        await self.db.delete(instance)
        await self.db.flush()

        # Invalidate cache
        await self._invalidate_manifest_cache(instance.uuid)

        # Trigger GC for potentially orphaned documents
        for doc_id in doc_ids_to_check:
            await self.trigger_garbage_collection_for_document(doc_id, embedding_module_id, engine_alias)

    async def on_resource_delete(self, resource: Resource) -> None:
        """
        Resource-level deletion. Just a metadata operation.
        """
        # Logic is handled by delete_instance cascade or explicit calls.
        # No physical drop collection needed anymore.
        pass

    # ==========================================================================
    # 2. Document Lifecycle (Ingestion Pipeline)
    # ==========================================================================

    async def add_document_to_version(self, instance_uuid: str, source_uri: str = None, file_name: str = "", file_content: str | list = None, payload: dict = None) -> DocumentRead:
        if not payload: payload = {}
        instance = await self._get_and_validate_workspace_instance(instance_uuid)
        
        async with self.db.begin_nested():
            if not file_name:
                file_name = Path(urlparse(source_uri).path).name if source_uri else ""
            
            document = KnowledgeDocument(source_uri=source_uri, file_name=file_name)
            self.db.add(document)
            await self.db.flush()
            
            await self.db.execute(
                insert(KnowledgeBaseVersionDocuments).values(version_id=instance.version_id, document_id=document.id)
            )
            await self.db.flush()

        await self._invalidate_manifest_cache(instance_uuid)
        await self.init_task_status(str(document.uuid))

        # Enqueue processing task
        await self.context.arq_pool.enqueue_job(
            'process_document_task', 
            instance.id, 
            document.id, 
            file_content, 
            payload, 
            self.context.actor.uuid
        )
        return DocumentRead.model_validate(document)

    async def remove_document_from_version(self, instance_uuid: str, document_uuid: str):
        instance = await self._get_and_validate_workspace_instance(instance_uuid)
        document = await self.document_dao.get_by_uuid(document_uuid)
        if not document: raise NotFoundError("Document not found.")

        stmt = delete(KnowledgeBaseVersionDocuments).where(
            KnowledgeBaseVersionDocuments.version_id == instance.version_id,
            KnowledgeBaseVersionDocuments.document_id == document.id
        )
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            raise NotFoundError("Document was not associated with this version.")
        await self.db.flush()

        await self._invalidate_manifest_cache(instance_uuid)
        
        # Trigger GC check
        await self.trigger_garbage_collection_for_document(
            document.id, 
            instance.embedding_module_version_id, 
            instance.engine_alias
        )

    async def _is_document_in_version(self, version_id: int, document_id: int) -> bool:
        query = select(func.count(text("1"))).select_from(KnowledgeBaseVersionDocuments).where(
            KnowledgeBaseVersionDocuments.version_id == version_id,
            KnowledgeBaseVersionDocuments.document_id == document_id
        )
        return await self.db.scalar(query) > 0

    async def update_document_in_version(self, instance_uuid: str, document_uuid_to_update: str, new_source_uri: Optional[str], new_file_name: Optional[str]) -> DocumentRead:
        instance = await self._get_and_validate_workspace_instance(instance_uuid)
        doc_to_update = await self.document_dao.get_one(where={"uuid": document_uuid_to_update})
        if not doc_to_update: raise NotFoundError("Document not found.")
        
        is_in_version = await self._is_document_in_version(instance.version_id, doc_to_update.id)
        if not is_in_version: raise NotFoundError("Document not found in this version.")

        if new_source_uri and new_source_uri != doc_to_update.source_uri:
            await self.remove_document_from_version(instance_uuid, document_uuid_to_update)
            return await self.add_document_to_version(instance_uuid, new_source_uri, new_file_name)
        elif new_file_name and new_file_name != doc_to_update.file_name:
            doc_to_update.file_name = new_file_name
            await self.db.flush()
            return DocumentRead.model_validate(doc_to_update)
        return DocumentRead.model_validate(doc_to_update)

    async def update_chunk_content(self, instance_uuid: str, batch_update_data: BatchChunkUpdate):
        instance = await self._get_and_validate_workspace_instance(instance_uuid)
        
        updates = batch_update_data.updates
        chunk_uuids_to_update = list(updates.keys())

        # --- 1. 预验证 (Upfront Validation) ---
        # 一次性查询所有相关的块，并加载它们的文档关系
        chunks_to_replace = await self.chunk_dao.get_list(
            where=[KnowledgeChunk.uuid.in_(chunk_uuids_to_update)],
            withs=["document"]
        )
        if len(chunks_to_replace) != len(chunk_uuids_to_update):
            found_uuids = {str(c.uuid) for c in chunks_to_replace}
            missing_uuids = set(chunk_uuids_to_update) - found_uuids
            raise NotFoundError(f"The following chunks were not found: {', '.join(missing_uuids)}")

        # 验证所有块是否属于同一个文档
        first_doc_id = chunks_to_replace[0].document_id
        original_doc = chunks_to_replace[0].document
        if any(c.document_id != first_doc_id for c in chunks_to_replace):
            raise ServiceException("All chunks in a batch update must belong to the same document.")
        
        # 验证该文档是否属于当前工作区版本
        is_in_version = await self._is_document_in_version(instance.version_id, first_doc_id)
        if not is_in_version:
            raise NotFoundError("The document containing these chunks does not belong to this knowledge base version.")
        
        # 重新加载完整的文档及其所有块，为复制做准备
        original_doc_full = await self.document_dao.get_by_pk(first_doc_id, withs=["chunks"])

        # --- 2. 执行写时复制 (Copy-on-Write) ---
        async with self.db.begin_nested():
            # 将UUID-content字典转换为ID-content字典，以便在复制时使用
            uuid_to_id_map = {str(c.uuid): c.id for c in chunks_to_replace}
            updates_by_id = {uuid_to_id_map[uuid]: content for uuid, content in updates.items()}
            
            new_document_clone = await self._copy_document_for_update(original_doc_full, updates_by_id)
            
            # 更新关联表，将工作区版本指向新的文档克隆
            update_assoc_stmt = (
                update(KnowledgeBaseVersionDocuments)
                .where(
                    KnowledgeBaseVersionDocuments.version_id == instance.version_id,
                    KnowledgeBaseVersionDocuments.document_id == original_doc_full.id
                )
                .values(document_id=new_document_clone.id)
            )
            await self.db.execute(update_assoc_stmt)
            
            # --- 3. 并发入队 (Concurrent Enqueueing) ---
            chunks_to_embed = [c for c in new_document_clone.chunks if c.status == ChunkProcessingStatus.PENDING]
            
            enqueue_tasks = []
            for chunk in chunks_to_embed:
                task = self.context.arq_pool.enqueue_job(
                    'update_chunk_task',
                    instance_id=instance.id,
                    chunk_id_to_embed=chunk.id,
                    user_uuid=self.context.actor.uuid
                )
                enqueue_tasks.append(task)
            
            await asyncio.gather(*enqueue_tasks)
            logger.info(f"Enqueued {len(enqueue_tasks)} chunk update tasks for new document {new_document_clone.id}.")

        # --- 4. 清理 ---
        await self._invalidate_manifest_cache(instance_uuid)
        await self.trigger_garbage_collection_for_document(original_doc_full.id, instance.embedding_module_version_id, instance.engine_alias)

    async def _copy_document_for_update(self, original_doc: KnowledgeDocument, updates: Dict[int, str]) -> KnowledgeDocument:
        """
        [NEW] Creates a deep copy of a document and its chunks, replacing one chunk's content.
        This is the core of our Copy-on-Write strategy.
        """
        # 1. Create a copy of the document metadata.
        new_doc = KnowledgeDocument(
            file_name=original_doc.file_name,
            file_type=original_doc.file_type,
            file_size=original_doc.file_size,
            source_uri=original_doc.source_uri,
            status=DocumentProcessingStatus.PROCESSING # The new doc needs processing
        )
        self.db.add(new_doc)
        await self.db.flush() # Get the ID for the new document

        # 2. Copy all chunks from the original, replacing the target one.
        new_chunks = []
        for old_chunk in original_doc.chunks:
            # 检查当前块是否在更新列表中
            if old_chunk.id in updates:
                # 是，则使用新内容创建块
                new_content = updates[old_chunk.id]
                new_chunks.append(KnowledgeChunk(
                    document_id=new_doc.id,
                    content=new_content,
                    token_count=len(new_content),
                    context=old_chunk.context,
                    payload=old_chunk.payload,
                    status=ChunkProcessingStatus.PENDING # 需要重新嵌入
                ))
            else:
                # 否，则直接复制旧块
                new_chunks.append(KnowledgeChunk(
                    document_id=new_doc.id,
                    content=old_chunk.content,
                    token_count=old_chunk.token_count,
                    context=old_chunk.context,
                    payload=old_chunk.payload,
                    vector_id=old_chunk.vector_id, 
                    status=ChunkProcessingStatus.COMPLETED
                ))
        
        self.db.add_all(new_chunks)
        await self.db.flush()
        
        await self.db.refresh(new_doc, attribute_names=['chunks'])
        return new_doc

    async def get_documents_in_version(self, instance_uuid: str, page: int, limit: int) -> PaginatedDocumentsResponse:
        instance = await self.get_by_uuid(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:read"], target=instance.resource.workspace)
        
        base_query = select(KnowledgeDocument).join(
            KnowledgeBaseVersionDocuments, KnowledgeDocument.id == KnowledgeBaseVersionDocuments.document_id
        ).where(KnowledgeBaseVersionDocuments.version_id == instance.version_id)
        
        total = await self.db.scalar(select(func.count()).select_from(base_query.subquery()))
        items = (await self.db.execute(base_query.order_by(KnowledgeDocument.created_at.desc()).limit(limit).offset((page - 1) * limit))).scalars().all()

        return PaginatedDocumentsResponse(items=[DocumentRead.model_validate(item) for item in items], total=total, page=page, limit=limit)

    # --- Task Status (Redis) ---
    def _get_status_key(self, document_uuid: str) -> str: return f"{self._TASK_STATUS_KEY_PREFIX}{document_uuid}"
    
    async def init_task_status(self, document_uuid: str):
        await self.redis.set_json(self._get_status_key(document_uuid), DocumentTaskProgress(status=DocumentProcessingStatus.PENDING, message="Queued").model_dump(mode='json'), expire=timedelta(days=1))

    async def update_task_progress(self, document_uuid: str, progress: DocumentTaskProgress):
        await self.redis.set_json(self._get_status_key(document_uuid), progress.model_dump(mode='json'), expire=timedelta(days=1))
        await self.redis.client.publish(self._TASK_PROGRESS_CHANNEL, document_uuid)

    async def get_task_progress(self, document_uuid: str) -> Optional[DocumentTaskProgress]:
        data = await self.redis.get_json(self._get_status_key(document_uuid))
        return DocumentTaskProgress.model_validate(data) if data else None

    # ==========================================================================
    # 3. Processing Pipeline (Worker)
    # ==========================================================================

    async def _process_document_pipeline(self, instance_id: int, document_id: int, file_content: str | list = None, payload: dict = None):
        """
        [WORKER LOGIC]
        Does parsing, chunking, embedding, and UPSERT into the shared collection.
        """
        if not payload: payload = {}
        session = self.db
        instance = await self.dao.get_by_pk(instance_id)
        if not instance: return # Orphaned
        workspace = instance.resource.workspace
        document = await self.document_dao.get_by_pk(document_id)
        if not document: return
        # [CRITICAL] payload includes document_uuid for filtering
        final_payload = {**payload, "document_uuid": str(document.uuid)}
        try:
            document.status = DocumentProcessingStatus.PROCESSING
            await session.flush()

            # 1. Determine Physical Collection
            engine_alias = instance.engine_alias
            collection_name = await self._resolve_physical_collection_name(instance.embedding_module_version_id)
            vector_engine = await self.context.vector_manager.get_engine(engine_alias)

            # 2. Cleanup old data (if retrying)
            # Filter by document_uuid is safe because document_uuid is unique
            await self.chunk_dao.delete_where({'document_id': document.id})
            await vector_engine.delete(collection_name=collection_name, filter_expr=f'payload["document_uuid"] == "{document.uuid}"')

            await self.update_task_progress(str(document.uuid), DocumentTaskProgress(status=document.status, message="Parsing..."))

            # 3. Parsing & Chunking
            config = KnowledgeBaseInstanceConfig.model_validate(instance.config or {})
            policy = BasePolicy(
                parser=ParserPolicy(**config.parser_policy.model_dump()) if config.parser_policy else None,
                chunkers=[ChunkerPolicy(**p.model_dump()) for p in config.chunker_policies]
            )
            all_chunks = await self.processing_pipeline.execute(policy=policy, file_url=document.source_uri, file_content=file_content)
            total_chunks = len(all_chunks)

            if not all_chunks:
                document.status = DocumentProcessingStatus.COMPLETED
                await session.flush()
                await self.update_task_progress(str(document.uuid), DocumentTaskProgress(status=document.status, message="Done (Empty)", progress=total_chunks, total=total_chunks))
                return

            # 4. Embedding
            texts_to_embed = [chunk.content for chunk in all_chunks]
            embedding_result = await self.embedding_service.generate_embedding(
                module_version_id=instance.embedding_module_version_id,
                workspace=workspace,
                texts=texts_to_embed
            )

            await self.update_task_progress(str(document.uuid), DocumentTaskProgress(status=document.status, message="Indexing...", progress=total_chunks, total=total_chunks))

            # 5. Upsert to Shared Collection
            vector_chunks_to_upsert = []
            orm_chunks_to_create = []
            has_failures = False

            for i, parsed_chunk in enumerate(all_chunks):
                emb_res = embedding_result.results[i]
                if emb_res.vector:
                    vector_id = str(uuid.uuid4())
                    vector_chunks_to_upsert.append(VectorChunk(
                        id=vector_id, vector=emb_res.vector,
                        payload={**final_payload, "content_preview": parsed_chunk.content[:200]}
                    ))
                    orm_chunks_to_create.append(KnowledgeChunk(
                        document_id=document.id, content=parsed_chunk.content, token_count=len(parsed_chunk.content),
                        vector_id=vector_id, context=parsed_chunk.metadata, payload=final_payload, status=ChunkProcessingStatus.COMPLETED
                    ))
                else:
                    has_failures = True
                    orm_chunks_to_create.append(KnowledgeChunk(
                        document_id=document.id, content=parsed_chunk.content, token_count=len(parsed_chunk.content),
                        vector_id=None, context=parsed_chunk.metadata, payload=final_payload, status=ChunkProcessingStatus.FAILED, 
                        error_message=emb_res.error_message or "Unknown embedding error."
                    ))

            if vector_chunks_to_upsert:
                # Ensure collection exists (Lazy check via system tool or assume pre-provisioned? 
                # Constraint said "System Collections must be pre-operated", so we assume it exists.
                # However, for safety in dev, we might catch error.)
                for i in range(0, len(vector_chunks_to_upsert), VECTOR_DB_UPSERT_BATCH_SIZE):
                    batch = vector_chunks_to_upsert[i:i + VECTOR_DB_UPSERT_BATCH_SIZE]
                    await vector_engine.upsert(collection_name, batch)

            session.add_all(orm_chunks_to_create)
            document.status = DocumentProcessingStatus.FAILED if has_failures else DocumentProcessingStatus.COMPLETED
            document.chunk_count = len(all_chunks)
            document.token_count = embedding_result.total_tokens
            document.processed_at = func.now()
            await session.flush()

            await self.update_task_progress(str(document.uuid), DocumentTaskProgress(
                status=document.status, message="Completed" if not has_failures else "Completed with errors", 
                progress=total_chunks, total=total_chunks, error=document.error_message
            ))

        except Exception as e:
            logger.error(f"Process failed doc {document_id}: {e}", exc_info=True)
            if document:
                document.status = DocumentProcessingStatus.FAILED
                document.error_message = str(e)[:1000]
                await session.flush()
                await self.update_task_progress(str(document.uuid), DocumentTaskProgress(status=document.status, message="Failed"))
            raise

    async def _update_chunk_pipeline(self, instance_id: int, chunk_id_to_embed: int):
        """Worker pipeline for re-embedding a single chunk."""
        instance = await self.dao.get_by_pk(instance_id)
        chunk = await self.chunk_dao.get_by_pk(chunk_id_to_embed, withs=["document"])
        if not instance or not chunk or not chunk.document: return

        collection_name = await self._resolve_physical_collection_name(instance.embedding_module_version_id)
        vector_engine = await self.context.vector_manager.get_engine(instance.engine_alias)

        try:
            workspace = instance.resource.workspace
            emb_res = await self.embedding_service.generate_embedding(
                module_version_id=instance.embedding_module_version_id, workspace=workspace, texts=[chunk.content]
            )
            
            if not emb_res.results[0].vector:
                raise ServiceException(emb_res.results[0].error_message)

            new_vector_id = str(uuid.uuid4())
            payload = chunk.payload or {}
            chunk_upsert = VectorChunk(
                id=new_vector_id, vector=emb_res.results[0].vector,
                payload={**payload, "document_uuid": str(chunk.document.uuid), "content_preview": chunk.content[:200]}
            )
            
            await vector_engine.upsert(collection_name, [chunk_upsert])

            chunk.vector_id = new_vector_id
            chunk.status = ChunkProcessingStatus.COMPLETED
            await self.db.flush()

        except Exception as e:
            chunk.status = ChunkProcessingStatus.FAILED
            chunk.error_message = str(e)[:500]
            await self.db.flush()
            raise

    # ==========================================================================
    # 3. Garbage Collection
    # ==========================================================================

    async def trigger_garbage_collection_for_document(self, document_id: int, embedding_module_version_id: int, engine_alias: str):
        """
        Triggers GC. Now requires explicit context (module version ID) to resolve collection name.
        """
        await self.context.arq_pool.enqueue_job(
            'garbage_collect_document_task', document_id, 
            str(embedding_module_version_id), # Pass as string to be safe with ARQ serialization
            engine_alias, self.context.actor.uuid
        )

    async def _is_document_orphaned(self, document_id: int) -> bool:
        check = await self.db.scalar(select(select(KnowledgeBaseVersionDocuments.version_id).where(KnowledgeBaseVersionDocuments.document_id == document_id).exists()))
        return not check

    async def _garbage_collect_document(self, document_id: int, embedding_module_version_id_str: str, engine_alias: str):
        """
        [Worker Logic]
        """
        async with self.db.begin_nested():
            doc = await self.db.get(KnowledgeDocument, document_id, with_for_update=True)
            if not doc: return

            if not await self._is_document_orphaned(doc.id):
                return

            collection_name = await self._resolve_physical_collection_name(int(embedding_module_version_id_str))
            
            # Delete vectors
            chunk_query = select(KnowledgeChunk.vector_id).where(KnowledgeChunk.document_id == doc.id)
            vector_ids = (await self.db.execute(chunk_query)).scalars().all()
            
            if vector_ids:
                try:
                    engine = await self.context.vector_manager.get_engine(engine_alias)
                    await engine.delete(collection_name, pks=vector_ids)
                except Exception as e:
                    logger.error(f"GC vector delete failed: {e}")
                    raise

            await self.db.delete(doc)

    async def run_periodic_gc(self):
        """
        Periodic GC.
        Assumption: We iterate orphaned documents. We need to know their last embedding model to clean up.
        Limitation: If `KnowledgeBaseVersionDocuments` is empty, we lost the link to `KnowledgeBase` and thus the `embedding_module_id`.
        Solution for future: Store `embedding_module_id` in `KnowledgeDocument` or `KnowledgeChunk`.
        Current Implementation: 
        We iterate chunks, find their vector_id (which might help if vector DB supports reverse lookup, but not generic).
        For now, this periodic task is a placeholder until we add `embedding_module_id` to `KnowledgeDocument`.
        It logs a warning.
        """
        logger.warning("Periodic GC is currently limited due to shared schema architecture migration. "
                       "Orphaned documents without history cannot be physically cleaned automatically yet.")
        pass

    # ==========================================================================
    # 4. Search & Execute (Routing & Aggregation)
    # ==========================================================================

    async def _get_version_document_manifest(self, instance: KnowledgeBase) -> List[str]:
        cache_key = self._get_manifest_cache_key(str(instance.uuid))
        cached = await self.redis.get_json(cache_key)
        if cached is not None: return cached

        query = select(KnowledgeDocument.uuid).join(
            KnowledgeBaseVersionDocuments, KnowledgeDocument.id == KnowledgeBaseVersionDocuments.document_id
        ).where(KnowledgeBaseVersionDocuments.version_id == instance.version_id)
        
        manifest = [str(u) for u in (await self.db.execute(query)).scalars().all()]
        await self.redis.set_json(cache_key, manifest, expire=timedelta(seconds=MANIFEST_CACHE_TTL_SECONDS))
        return manifest

    async def execute(self, instance_uuid: str, execute_params: KnowledgeBaseExecutionRequest, actor: User, runtime_workspace: Optional[Workspace] = None) -> KnowledgeBaseExecutionResponse:
        results = await self.execute_batch([instance_uuid], execute_params, actor, runtime_workspace)
        return results[0]

    async def execute_batch(self, instance_uuids: List[str], execute_params: KnowledgeBaseExecutionRequest, actor: User, runtime_workspace: Optional[Workspace] = None) -> List[KnowledgeBaseExecutionResponse]:
            """
            [High Performance] Executes search across multiple instances in PARALLEL with Map-Reduce pattern.
            """
            inputs = execute_params.inputs
            
            # 0. Fast Fail
            if not inputs.query.strip() or not instance_uuids:
                return [
                    KnowledgeBaseExecutionResponse(data=GroupedSearchResult(instance_uuid=uid, chunks=[])) 
                    for uid in instance_uuids
                ]

            async with TraceManager(self.db, "knowledge.batch_search", actor.id, attributes=KnowledgeBaseAttributes(inputs=inputs)) as span:
                
                # 1. Batch Load & Validate
                instances = await self.dao.get_by_uuids(instance_uuids)
                if len(instances) != len(set(instance_uuids)):
                    # Improve error message to show which UUIDs are missing
                    found_uuids = {inst.uuid for inst in instances}
                    missing = set(instance_uuids) - found_uuids
                    raise NotFoundError(f"KnowledgeBase instances not found: {missing}")

                # 2. Grouping, Reverse Indexing & Pre-aggregation (Single Pass)
                # Physical Group Key: (engine_alias, embedding_module_version_id)
                # Value: (List[KnowledgeBase], Set[doc_uuid])
                physical_groups = defaultdict(lambda: ([], set()))
                
                doc_to_instances_map = defaultdict(set)

                for inst in instances:
                    await self._check_execute_perm(inst)
                    physical_key = (inst.engine_alias, inst.embedding_module_version_id)
                    group_list, group_manifest = physical_groups[physical_key]
                    
                    group_list.append(inst)

                    # [Optimization] Call manifest once per instance
                    manifest = await self._get_version_document_manifest(inst)
                    
                    # Update Reverse Index
                    for doc_uuid in manifest:
                        doc_to_instances_map[doc_uuid].add(inst.uuid)
                    
                    # Update Physical Filter Set
                    group_manifest.update(manifest)

                # 3. Parallel Execution (Map Phase)
                async def _search_physical_group(key, group_instances, pre_aggregated_manifest):
                    engine_alias, emb_ver_id = key
                    
                    if not pre_aggregated_manifest: return []

                    # A. Determine Billing Workspace
                    # Use runtime_workspace if provided, otherwise pick the first instance's workspace
                    ws = runtime_workspace or group_instances[0].resource.workspace

                    # B. Embedding (Billing occurs here)
                    emb_res = await self.embedding_service.generate_embedding(
                        module_version_id=emb_ver_id, workspace=ws, texts=[inputs.query]
                    )

                    if not emb_res.results or not emb_res.results[0].vector:
                        return []
                    query_vec = emb_res.results[0].vector

                    # C. Physical Search
                    collection_name = await self._resolve_physical_collection_name(emb_ver_id)
                    try:
                        engine = await self.context.vector_manager.get_engine(engine_alias)
                        
                        # Mandatory Filter
                        doc_ids_json = json.dumps(list(pre_aggregated_manifest))
                        filter_expr = f'payload["document_uuid"] in {doc_ids_json}'
                        
                        # Fetch candidates
                        return await engine.search(
                            collection_name, query_vec, 
                            top_k=inputs.config.max_recall_num, 
                            filter_expr=filter_expr
                        )
                    except Exception as e:
                        logger.error(f"Physical search failed for {collection_name}: {e}")
                        return []

                # Launch Tasks
                tasks = [
                    _search_physical_group(key, grp_data[0], grp_data[1]) 
                    for key, grp_data in physical_groups.items()
                ]
                
                group_results_list = await asyncio.gather(*tasks)

                # 4. Global Merge & Sort (Reduce Phase)
                all_physical_results = []
                for res_list in group_results_list:
                    all_physical_results.extend(res_list)
                
                # Sort DESC
                all_physical_results.sort(key=lambda x: x.score, reverse=True)
                
                # Global Cutoff
                winners = all_physical_results[:inputs.config.max_recall_num]

                # 5. Optimized Hydration
                per_instance_results = {uid: [] for uid in instance_uuids}

                if winners:
                    vector_ids = [r.id for r in winners]
                    chunks = await self.chunk_dao.get_list(where=[KnowledgeChunk.vector_id.in_(vector_ids)])
                    chunk_map = {c.vector_id: c for c in chunks}

                    for res in winners:
                        chunk = chunk_map.get(res.id)
                        doc_uuid = res.payload.get("document_uuid")
                        # Robustness check: payload MUST contain document_uuid
                        if not chunk or not doc_uuid: 
                            continue

                        owner_instances = doc_to_instances_map.get(doc_uuid, set())
                        
                        result_item = SearchResultChunk(
                            uuid=chunk.uuid, content=chunk.content, score=res.score, context=chunk.context
                        )

                        for owner_uuid in owner_instances:
                            if owner_uuid in per_instance_results:
                                per_instance_results[owner_uuid].append(result_item)

                # 6. Final Response Construction
                final_responses = []
                for uid in instance_uuids:
                    chunk_list = per_instance_results[uid]
                    # Re-sort to guarantee order within each group
                    chunk_list.sort(key=lambda x: x.score, reverse=True)
                    
                    final_responses.append(KnowledgeBaseExecutionResponse(
                        data=GroupedSearchResult(instance_uuid=uid, chunks=chunk_list)
                    ))

                span.set_output([r.data for r in final_responses])
                return final_responses

    # --- 5. Helpers ---
    async def _get_and_validate_workspace_instance(self, instance_uuid: str) -> KnowledgeBase:
        instance = await self.get_by_uuid(instance_uuid)
        if not instance: raise NotFoundError("KnowledgeBase instance not found.")
        if instance.status != VersionStatus.WORKSPACE: raise ServiceException("Operation only allowed on workspace version.")
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=instance.resource.workspace)
        return instance

    async def validate_instance(self, instance: KnowledgeBase) -> ValidationResult:
        doc_count = await self.db.scalar(select(func.count()).select_from(KnowledgeBaseVersionDocuments).where(KnowledgeBaseVersionDocuments.version_id == instance.version_id))
        if doc_count == 0:
            return ValidationResult(is_valid=False, errors=["Knowledge base must contain at least one document."])
        return ValidationResult(is_valid=True, errors=[])
    
    async def get_dependencies(self, instance: KnowledgeBase) -> List[DependencyInfo]:
        return [DependencyInfo(resource_uuid="sys", instance_uuid=str(instance.embedding_module_version_id), alias="embedding_model")]

    async def get_searchable_content(self, instance: KnowledgeBase) -> str:
        return f"{instance.name} {instance.description or ''}"

    async def as_llm_tool(self, instance: KnowledgeBase) -> Optional[LLMTool]:
        return None
