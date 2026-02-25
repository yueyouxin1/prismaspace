# src/app/engine/model/embedding/clients/openai_client.py

import openai
from openai import APIError, RateLimitError, AuthenticationError, APITimeoutError, BadRequestError
from typing import List
from ..base import (
    EmbeddingProviderConfig, EmbeddingRunConfig, EmbeddingResult, 
    BatchEmbeddingResult, EmbeddingEngineError, EmbeddingAuthenticationError, 
    EmbeddingRateLimitError, EmbeddingBadRequestError
)
# 从 main.py 导入注册器 (稍后创建)
from ..main import register_embedding_client

@register_embedding_client("openai")
class OpenAIEmbeddingClient:
    """使用 'openai' Python SDK 的 Embedding 客户端实现。"""

    def __init__(self, config: EmbeddingProviderConfig):
        try:
            self.client = openai.AsyncOpenAI(
                api_key=config.api_key,
                base_url=str(config.base_url) if config.base_url else None,
                timeout=config.timeout,
                max_retries=config.max_retries,
            )
        except Exception as e:
            raise EmbeddingEngineError(f"Failed to initialize OpenAI client: {e}")

    async def embed_batch(
        self,
        texts: List[str],
        run_config: EmbeddingRunConfig
    ) -> BatchEmbeddingResult:
        
        # 准备 API 请求参数，并移除 None 值
        api_params = {
            "model": run_config.model,
            "input": texts,
            "dimensions": run_config.dimensions
        }
        api_params = {k: v for k, v in api_params.items() if v is not None}

        try:
            # 发起 API 调用
            response = await self.client.embeddings.create(**api_params)
            
            # 将 SDK 的返回结果转换为我们标准化的 EmbeddingResult 列表
            embedding_results = [
                EmbeddingResult(
                    index=data.index,
                    vector=data.embedding
                )
                for data in response.data
            ]
            
            # 封装成标准的批量结果对象
            return BatchEmbeddingResult(
                results=embedding_results,
                total_tokens=response.usage.total_tokens
            )

        except AuthenticationError as e:
            raise EmbeddingAuthenticationError(f"OpenAI authentication failed: {e.message}")
        except RateLimitError as e:
            raise EmbeddingRateLimitError(f"OpenAI rate limit exceeded: {e.message}")
        except BadRequestError as e:
            raise EmbeddingBadRequestError(f"Invalid request to OpenAI: {e.message}")
        except (APIError, APITimeoutError) as e:
            raise EmbeddingEngineError(f"OpenAI API error: {e.message}")
        except Exception as e:
            # 捕获任何其他意外错误
            raise EmbeddingEngineError(f"An unexpected error occurred in OpenAIEmbeddingClient: {str(e)}")