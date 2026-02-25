# src/app/schemas/resource/knowledge/knowledge_schemas.py

from pydantic import BaseModel, Field, ConfigDict, HttpUrl, model_validator, conint, confloat
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from app.schemas.common import ExecutionRequest, ExecutionResponse
from app.models.resource.knowledge import DocumentProcessingStatus
from app.core.config import settings

class DocumentRead(BaseModel):
    uuid: str
    file_name: str
    source_uri: HttpUrl
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    status: DocumentProcessingStatus
    error_message: Optional[str] = None
    chunk_count: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class PaginatedDocumentsResponse(BaseModel):
    items: List[DocumentRead]
    total: int
    page: int
    limit: int

class DocumentTaskProgress(BaseModel):
    """
    一个标准化的模型，用于在Redis中存储和通过SSE发送任务进度。
    """
    status: DocumentProcessingStatus
    message: str
    progress: int = 0  # e.g., chunks processed
    total: int = 0     # e.g., total chunks
    error: Optional[str] = None

# 用于向版本中添加或更新文档的Schema
class DocumentCreate(BaseModel):
    source_uri: HttpUrl = Field(..., description="The URL of the document in object storage.")
    file_name: Optional[str] = Field(None, description="Optional: A display name for the file. If not provided, it will be inferred from the URL.")

# 用于更新文档的Schema，现在职责更清晰
class DocumentUpdate(BaseModel):
    source_uri: Optional[HttpUrl] = Field(None, description="A new URL to completely replace the document's content.")
    file_name: Optional[str] = Field(None, description="A new display name for the file.")

# 用于更新单个文档块内容的Schema
class ChunkUpdate(BaseModel):
    content: str = Field(..., min_length=1, description="The new text content for the chunk.")

class BatchChunkUpdate(BaseModel):
    updates: Dict[str, str] = Field(..., description="A dictionary where keys are chunk UUIDs and values are the new content.")

    @model_validator(mode='before')
    @classmethod
    def check_not_empty(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get('updates'):
            raise ValueError("The 'updates' dictionary cannot be empty.")
        return data

class RAGConfig(BaseModel):
    """RAG (检索增强生成) 配置类"""
    
    # 召回配置
    max_recall_num: conint(ge=1, le=20) = Field(
        default=5,
        description="最大召回段落数"
    )
    min_match_score: confloat(ge=0.0, le=1.0) = Field(
        default=0.5,
        description="最小匹配度阈值 (0-1)"
    )
    
    # 搜索策略
    search_strategy: Literal["keyword", "semantic", "hybrid"] = Field(
        default="hybrid",
        description="搜索策略: 关键词、语义或混合"
    )
    
    # 高级功能
    query_rewrite: bool = Field(default=False, description="是否启用查询改写 (Query Rewriting)")
    result_rerank: bool = Field(default=False, description="是否启用结果重排 (Reranking)")

class KnowledgeBaseExecutionParams(BaseModel):
    query: str = Field(..., description="查询内容")
    config: RAGConfig = Field(default_factory=RAGConfig)

class KnowledgeBaseExecutionRequest(ExecutionRequest):
    inputs: KnowledgeBaseExecutionParams = Field(..., description="运行时参数")

class SearchResultChunk(BaseModel):
    uuid: str
    content: str
    score: float
    context: Optional[Dict[str, Any]] = None

class GroupedSearchResult(BaseModel):
    instance_uuid: str
    chunks: List[SearchResultChunk]

class KnowledgeBaseExecutionResponse(ExecutionResponse):
    success: bool = True
    data: GroupedSearchResult

class ParserPolicyConfig(BaseModel):
    parser_name: str = Field(default="simple_parser_v1")
    allowed_mime_types: List[str] = Field(default_factory=lambda: ["text/plain", "application/pdf"])
    params: Dict[str, Any] = Field(default_factory=lambda: {"tika_url": str(settings.TIKA_SERVER_URL)})

class ChunkerPolicyConfig(BaseModel):
    chunker_name: str
    params: Dict[str, Any] = Field(default_factory=dict)

class KnowledgeBaseInstanceConfig(BaseModel):
    parser_policy: Optional[ParserPolicyConfig] = Field(default_factory=ParserPolicyConfig)
    chunker_policies: List[ChunkerPolicyConfig] = Field(
        default_factory=lambda: [
            ChunkerPolicyConfig(chunker_name="simple_chunker_v1", params={"chunk_size": 500}),
            ChunkerPolicyConfig(chunker_name='html_chunker_v1')
        ]
    )

class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[KnowledgeBaseInstanceConfig] = None

class KnowledgeBaseRead(BaseModel):
    uuid: str
    name: str
    version_tag: str
    status: str
    config: KnowledgeBaseInstanceConfig
    document_count: int = Field(..., description="The number of documents in this version.")
    model_config = ConfigDict(from_attributes=True, extra="ignore")