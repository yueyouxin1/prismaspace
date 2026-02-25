# src/app/schemas/resource/execution_schemas.py

from typing import Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict
from .tool_schemas import ToolExecutionRequest, ToolExecutionResponse
from .tenantdb.tenantdb_schemas import TenantDbExecutionRequest, TenantDbExecutionResponse
from .knowledge.knowledge_schemas import KnowledgeBaseExecutionRequest, KnowledgeBaseExecutionResponse

AnyExecutionRequest = Union[
    ToolExecutionRequest,
    TenantDbExecutionRequest,
    KnowledgeBaseExecutionRequest,
    # <-- 未来扩展
]

AnyExecutionResponse = Union[
    ToolExecutionResponse,
    TenantDbExecutionResponse,
    KnowledgeBaseExecutionResponse,
    # <-- 未来扩展
]
