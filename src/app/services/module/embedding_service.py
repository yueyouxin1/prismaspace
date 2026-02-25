# src/app/services/module/embedding_service.py

import logging
from typing import List, Dict, Optional, Union
from decimal import Decimal

from app.core.context import AppContext
from app.services.base_service import BaseService
from app.models import Workspace
from app.services.module.service_module_service import ServiceModuleService
from app.services.billing.context import BillingContext
from app.services.product.types.feature import FeatureRole
from app.services.module.types.specifications import EmbeddingAttributes
from app.services.exceptions import ConfigurationError
from app.engine.model.embedding import (
    EmbeddingEngineService, EmbeddingProviderConfig, EmbeddingRunConfig, 
    BatchEmbeddingResult, EmbeddingResult, VectorCache
)

logger = logging.getLogger(__name__)

class EmbeddingService(BaseService):
    """
    [Core Service] 负责向量生成的 缓存管理、计费拦截 和 引擎调用。
    """
    def __init__(self, context: AppContext, cache: Optional[VectorCache] = None):
        self.context = context
        # [STEP 0] 初始化共享 VectorCache 并挂载到 Context
        # 这是所有下游服务共享缓存的关键
        # 依赖注入缓存容器
        if not self.context.vector_cache:
            self.context.vector_cache = VectorCache()
        self.cache = self.context.vector_cache
        self.module_service = ServiceModuleService(context)
        self.engine = EmbeddingEngineService()

    async def generate_embedding(
        self, 
        module_version_id: int, 
        workspace: Workspace, 
        texts: List[str]
    ) -> BatchEmbeddingResult:
        """
        生成向量。自动处理缓存命中，复用 EmbeddingResult。
        """
        if not texts:
            return BatchEmbeddingResult(results=[], total_tokens=0)

        # 1. 检查缓存，分离 Hits 和 Misses
        results_map: Dict[int, EmbeddingResult] = {} # index -> result
        texts_to_embed: List[str] = []
        
        # 记录 miss 的文本在原始列表中的索引
        # miss_indices[k] = original_index_in_texts
        miss_indices: List[int] = [] 

        for i, text in enumerate(texts):
            cached_res = self.cache.get(module_version_id, text)
            if cached_res:
                # [CRITICAL] 缓存命中：
                # 缓存中的对象 index 是旧的，必须拷贝并更新为当前列表的 index (i)
                # 这样才能保证 BatchEmbeddingResult 的顺序正确性
                new_res = cached_res.model_copy(update={"index": i})
                results_map[i] = new_res
            else:
                # 缓存未命中：加入待处理队列
                texts_to_embed.append(text)
                miss_indices.append(i)

        total_tokens_billed = 0

        # 2. 如果有未命中的文本，调用引擎并计费
        if texts_to_embed:
            # 获取 Module 运行时上下文 (包含 Credential)
            module_runtime_context = await self.module_service.get_runtime_context(
                module_version_id, self.context.actor, workspace
            )
            is_custom_credential = module_runtime_context.credential.is_custom
            
            try:
                attributes = EmbeddingAttributes.model_validate(module_runtime_context.version.attributes)
            except Exception:
                 raise ConfigurationError(f"Embedding module version {module_version_id} attributes invalid.")

            # 获取计费 Feature
            embedding_token_feature = None
            if not is_custom_credential:
                embedding_token_feature = next(
                    (f for f in module_runtime_context.version.features if f.feature_role == FeatureRole.EMBEDDING_TOKEN), 
                    None
                )
                if not embedding_token_feature:
                    raise ConfigurationError(f"Embedding module '{module_runtime_context.version.name}' missing EMBEDDING_TOKEN feature.")

            # --- 计费 & 执行 (针对 Misses) ---
            billing_entity = workspace.billing_owner
            async with BillingContext(self.context, billing_entity) as bc:
                # 预估成本 (仅针对未命中的文本)
                receipt = None
                if embedding_token_feature:
                    reserve_usage = Decimal(sum(len(t) for t in texts_to_embed))
                    receipt = await bc.reserve(feature=embedding_token_feature, reserve_usage=reserve_usage)

                # 执行物理嵌入
                provider_config = EmbeddingProviderConfig(
                    client_name=attributes.client_name, 
                    base_url=module_runtime_context.credential.endpoint, 
                    api_key=module_runtime_context.credential.api_key
                )
                run_config = EmbeddingRunConfig(
                    model=module_runtime_context.version.name, 
                    dimensions=attributes.dimensions,
                    max_batch_size=attributes.max_batch_size,
                    max_batch_tokens=attributes.max_batch_tokens
                )

                # 调用底层无状态引擎
                api_result = await self.engine.run_batch(provider_config, run_config, texts_to_embed)
                
                # 实报实销
                if embedding_token_feature and receipt:
                    actual_usage = Decimal(api_result.total_tokens)
                    await bc.report_usage(
                        receipt=receipt, feature=embedding_token_feature, actual_usage=actual_usage
                    )
                
                total_tokens_billed = api_result.total_tokens

                # 3. 处理 API 结果：回填 Cache 并合并到结果集
                for res in api_result.results:
                    # 获取该结果对应的原始索引
                    # api_result.results 中的 index 是相对于 texts_to_embed 的 (0, 1, 2...)
                    # 我们需要将其映射回原始 texts 列表的索引
                    original_idx = miss_indices[res.index]
                    
                    # 存入 Cache (Key是文本, Value是完整结果)
                    # 注意：存入 Cache 的 EmbeddingResult 的 index 字段实际上在后续 get 时会被覆盖，
                    # 但为了数据完整性，我们存入时不修改它，或者将其 update 为 generic 0 也可以。
                    # 这里保持 API 返回的原样存入即可。
                    if res.vector:
                        self.cache.set(module_version_id, texts[original_idx], res)
                    
                    # 修正 index 为原始索引，放入 Map 返回给调用者
                    # 必须 copy，不能修改 res 本身，因为 res 可能刚被放入 cache
                    res_for_return = res.model_copy(update={"index": original_idx})
                    results_map[original_idx] = res_for_return

        # 4. 组装最终结果列表 (按原始顺序)
        final_results = []
        for i in range(len(texts)):
            if i in results_map:
                final_results.append(results_map[i])
            else:
                # 防御性代码：如果不幸丢失（例如API报错部分丢失），填充错误
                final_results.append(EmbeddingResult(index=i, error_message="Internal processing error: result missing"))

        if texts_to_embed:
            logger.debug(f"[CachedEmbedding] Batch: {len(texts)}, Hits: {len(texts)-len(texts_to_embed)}, Misses: {len(texts_to_embed)}")

        return BatchEmbeddingResult(
            results=final_results,
            total_tokens=total_tokens_billed # 注意：这里只返回了实际消耗/计费的 tokens
        )