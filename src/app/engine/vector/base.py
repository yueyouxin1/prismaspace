# engine/vector/base.py
from abc import ABC, abstractmethod
from typing import Literal, List, Dict, Any, NamedTuple

class VectorEngineConfig(NamedTuple):
    """
    一个标准的、可序列化的模型，用于定义任何向量引擎的连接配置。
    """
    engine_type: Literal["milvus", "qdrant"] # 未来可扩展
    host: str
    port: int
    alias: str # 此配置的唯一别名，如 'default_milvus', 'high_perf_qdrant'

class VectorChunk(NamedTuple):
    id: str
    vector: List[float]
    payload: Dict[str, Any] # Will store content and other metadata

class SearchResult(NamedTuple):
    id: str
    score: float
    payload: Dict[str, Any]

class VectorEngineError(Exception):
    """Base exception for all engine errors"""
    pass
    
class VectorEngineService(ABC):
    """
    The abstract interface for all vector database operations.
    It is completely stateless and works with native Python types.
    """
    @abstractmethod
    async def create_collection(self, name: str, vector_size: int):
        raise NotImplementedError

    @abstractmethod
    async def delete_collection(self, name: str):
        raise NotImplementedError

    @abstractmethod
    async def list_collections(self) -> List[str]:
        """List all collection names in the vector database."""
        raise NotImplementedError
        
    @abstractmethod
    async def insert(self, collection_name: str, chunks: List[VectorChunk]):
        raise NotImplementedError

    @abstractmethod
    async def upsert(self, collection_name: str, chunks: List[VectorChunk]):
        raise NotImplementedError

    @abstractmethod
    async def delete(self, collection_name: str, pks: List[str], filter_expr: str) -> int:
        raise NotImplementedError

    @abstractmethod
    async def query(self, collection_name: str, pks: List[str], filter_expr: str, output_fields: List[str]) -> List[VectorChunk]:
        raise NotImplementedError

    @abstractmethod
    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        top_k: int
    ) -> List[SearchResult]:
        raise NotImplementedError