# src/app/engine/model/embedding/__init__.py
from .main import EmbeddingEngineService, VectorCache
from .base import (
    EmbeddingProviderConfig,
    EmbeddingRunConfig,
    EmbeddingResult,
    BatchEmbeddingResult,
    EmbeddingEngineError,
    EmbeddingAuthenticationError,
    EmbeddingRateLimitError,
    EmbeddingProviderNotFoundError,
    EmbeddingBadRequestError
)

# 确保客户端被加载和注册
from . import clients