# src/app/engine/model/embedding/main.py

import hashlib
import logging # 引入日志
from typing import Optional, Dict, Type, List, Tuple
from .base import (
    BaseEmbeddingClient, EmbeddingProviderConfig, EmbeddingRunConfig,
    EmbeddingResult, BatchEmbeddingResult, EmbeddingEngineError, EmbeddingProviderNotFoundError
)

# --- 客户端注册表 ---
_embedding_clients_registry: Dict[str, Type[BaseEmbeddingClient]] = {}

def register_embedding_client(client_name: str):
    """一个装饰器，用于将具体的客户端实现注册到工厂中。"""
    def decorator(cls: Type[BaseEmbeddingClient]):
        _embedding_clients_registry[client_name] = cls
        return cls
    return decorator


class VectorCache:
    """
    [Request-Scoped Cache]
    一个简单的内存缓存容器，在一次请求（Trace）生命周期内共享 EmbeddingResult。
    """
    def __init__(self):
        # Key: f"{module_version_id}:{md5(text)}"
        # Value: EmbeddingResult (Snapshot)
        self._cache: Dict[str, EmbeddingResult] = {}
        self.hits: int = 0
        self.misses: int = 0

    def _get_key(self, version_id: int, text: str) -> str:
        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        return f"{version_id}:{text_hash}"

    def get(self, version_id: int, text: str) -> Optional[EmbeddingResult]:
        """
        获取缓存的 EmbeddingResult。
        注意：返回的是原始缓存对象，调用者需要根据当前上下文修正其 `index` 字段。
        """
        key = self._get_key(version_id, text)
        val = self._cache.get(key)
        if val:
            self.hits += 1
        else:
            self.misses += 1
        return val

    def set(self, version_id: int, text: str, result: EmbeddingResult):
        key = self._get_key(version_id, text)
        # 我们缓存一份拷贝，以防外部修改
        self._cache[key] = result

    def clear(self):
        self._cache.clear()
        self.hits = 0
        self.misses = 0

class EmbeddingEngineService:
    """
    纯粹的、无状态的 Embedding 执行引擎。
    它根据传入的配置动态选择并初始化正确的客户端来执行任务。
    """
    
    def _get_client(self, config: EmbeddingProviderConfig) -> BaseEmbeddingClient:
        """
        工厂方法：根据提供商名称查找并实例化客户端。
        """
        client_name = config.client_name
        client_class = _embedding_clients_registry.get(client_name)
        
        if not client_class:
            raise EmbeddingProviderNotFoundError(f"No Embedding client registered for provider '{client_name}'. "
                             f"Available providers: {list(_embedding_clients_registry.keys())}")
        
        # 每次调用都创建一个新的客户端实例，以确保配置隔离
        return client_class(config)

    def _plan_batches(
        self,
        texts: List[str],
        max_batch_size: int,
        max_batch_tokens: int
    ) -> Tuple[List[List[Tuple[int, str]]], List[EmbeddingResult]]:
        """
        智能地规划批处理任务。

        返回:
            - 一个包含`(原始索引, 文本)`元组的批处理列表。
            - 一个因为文本超长而预先失败的 EmbeddingResult 列表。
        """
        valid_batches: List[List[Tuple[int, str]]] = []
        pre_failed_results: List[EmbeddingResult] = []
        
        current_batch: List[Tuple[int, str]] = []
        current_batch_tokens = 0
        max_tokens = max_batch_tokens

        for i, text in enumerate(texts):
            token_estimate = len(text)

            # 预检查单个文本是否超长
            if token_estimate > max_tokens:
                pre_failed_results.append(EmbeddingResult(
                    index=i,
                    error_message=f"Text is too long ({token_estimate} chars) to be processed. Maximum is {max_tokens}."
                ))
                continue

            is_first_item = not current_batch
            batch_not_full = len(current_batch) < max_batch_size
            tokens_not_exceeded = (current_batch_tokens + token_estimate) <= max_tokens

            if is_first_item or (batch_not_full and tokens_not_exceeded):
                current_batch.append((i, text))
                current_batch_tokens += token_estimate
            else:
                valid_batches.append(current_batch)
                current_batch = [(i, text)]
                current_batch_tokens = token_estimate

        if current_batch:
            valid_batches.append(current_batch)

        return valid_batches, pre_failed_results

    async def run_batch(
        self,
        provider_config: EmbeddingProviderConfig,
        run_config: EmbeddingRunConfig,
        texts: List[str],
    ) -> BatchEmbeddingResult:
        if not texts:
            return BatchEmbeddingResult(results=[], total_tokens=0)
            
        client = self._get_client(provider_config)
        valid_batches, pre_failed_results = self._plan_batches(texts, run_config.max_batch_size, run_config.max_batch_tokens)

        all_results: List[EmbeddingResult] = pre_failed_results
        total_tokens = 0

        for batch_with_indices in valid_batches:
            batch_texts = [text for _, text in batch_with_indices]
            
            try:
                # [关键新增] 捕获批处理级别的异常
                batch_result = await client.embed_batch(texts=batch_texts, run_config=run_config)
                
                # [关键新增] 校验返回结果长度是否匹配
                if len(batch_result.results) != len(batch_texts):
                    error_msg = f"API returned {len(batch_result.results)} embeddings for {len(batch_texts)} inputs."
                    raise EmbeddingEngineError(error_msg)

                # 成功：重新计算原始索引并添加到总结果中
                for res in batch_result.results:
                    original_index = batch_with_indices[res.index][0]
                    res.index = original_index
                    all_results.append(res)
                
                total_tokens += batch_result.total_tokens

            except Exception as e:
                # 失败：为这个批次的所有文本创建失败结果
                error_msg = f"Batch failed due to API error: {e}"
                logging.warning(error_msg, exc_info=True)
                for original_index, _ in batch_with_indices:
                    all_results.append(EmbeddingResult(
                        index=original_index,
                        error_message=error_msg
                    ))

        # 确保结果按原始索引排序
        all_results.sort(key=lambda r: r.index)
        
        return BatchEmbeddingResult(results=all_results, total_tokens=total_tokens)