# src/app/engine/model/embedding/base.py

from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl

# --- 引擎层标准异常 ---

class EmbeddingEngineError(Exception):
    """Embedding引擎所有错误的基类"""
    pass

class EmbeddingAuthenticationError(EmbeddingEngineError):
    """凭证无效或权限不足"""
    pass

class EmbeddingRateLimitError(EmbeddingEngineError):
    """达到API频率限制"""
    pass

class EmbeddingBadRequestError(EmbeddingEngineError):
    """请求参数无效"""
    pass

class EmbeddingProviderNotFoundError(EmbeddingEngineError):
    """当找不到指定的 Embedding provider 时抛出"""
    pass

# --- 数据模型 (Data Models) ---

class EmbeddingProviderConfig(BaseModel):
    """
    定义了调用 Embedding 模型所需的客户端和凭证信息。
    """
    client_name: str = Field(..., description="客户端的唯一标识符, e.g., 'openai', 'zhipu'")
    api_key: str = Field(..., description="API Key")
    base_url: Optional[HttpUrl] = Field(None, description="API的基础URL，用于代理或私有部署")
    timeout: int = Field(60, description="API请求的超时时间（秒）")
    max_retries: int = Field(2, description="API请求的最大重试次数")

class EmbeddingRunConfig(BaseModel):
    """
    定义单次 Embedding 运行的配置。
    """
    model: str
    dimensions: Optional[int] = Field(None, description="（可选）嵌入向量的维度，仅部分新模型支持")
    max_batch_size: int = Field(1, description="The maximum number of texts that can be processed in a single batch.")
    max_batch_tokens: int = Field(1024, description="The maximum total tokens allowed in a single batch request.")

class EmbeddingResult(BaseModel):
    """
    标准化的单个嵌入结果。
    """
    index: int = Field(..., description="结果在原始输入列表中的索引")
    vector: Optional[List[float]] = Field(None, description="生成的嵌入向量，如果失败则为None")
    error_message: Optional[str] = Field(None, description="如果嵌入失败，记录错误信息")

class BatchEmbeddingResult(BaseModel):
    """
    标准化的批量嵌入结果，包含结果列表和用量信息。
    """
    results: List[EmbeddingResult]
    total_tokens: int

# --- 客户端接口 (Client Interface - Strategy Pattern) ---

class BaseEmbeddingClient(ABC):
    """
    所有具体 Embedding SDK 实现的统一接口。
    """
    def __init__(self, config: EmbeddingProviderConfig):
        ...

    @abstractmethod
    async def embed_batch(
        self,
        texts: List[str],
        run_config: EmbeddingRunConfig
    ) -> BatchEmbeddingResult:
        """
        执行批量嵌入的核心方法。
        接收一个文本列表，返回一个标准的批量结果对象。
        """
        ...