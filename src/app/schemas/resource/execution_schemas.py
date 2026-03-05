# src/app/schemas/resource/execution_schemas.py

from __future__ import annotations

from typing import Any, Dict, List, TypeAlias, Type, Union

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ExecutionRequest, ExecutionResponse
from .agent.agent_schemas import AgentExecutionRequest, AgentExecutionResponse
from .knowledge.knowledge_schemas import (
    KnowledgeBaseExecutionRequest,
    KnowledgeBaseExecutionResponse,
)
from .tenantdb.tenantdb_schemas import TenantDbExecutionRequest, TenantDbExecutionResponse
from .tool_schemas import ToolExecutionRequest, ToolExecutionResponse
from .workflow.workflow_schemas import WorkflowExecutionRequest, WorkflowExecutionResponse


class GenericExecutionRequest(ExecutionRequest):
    """
    内部兜底执行请求模型。
    用于调用方在不知道具体资源执行参数模型时，仍可通过统一契约传递 inputs/meta。
    """

    model_config = ConfigDict(extra="forbid")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Generic runtime inputs.")


class GenericExecutionResponse(ExecutionResponse):
    """
    内部兜底执行响应模型。
    仅用于统一接口及内部编排场景，不替代资源专属响应模型。
    """

    model_config = ConfigDict(extra="forbid")
    data: Any = Field(default_factory=dict, description="Generic execution output.")


AnyExecutionRequest: TypeAlias = Union[
    AgentExecutionRequest,
    TenantDbExecutionRequest,
    KnowledgeBaseExecutionRequest,
    ToolExecutionRequest,
    WorkflowExecutionRequest,
    GenericExecutionRequest,
]

AnyExecutionResponse: TypeAlias = Union[
    AgentExecutionResponse,
    TenantDbExecutionResponse,
    KnowledgeBaseExecutionResponse,
    ToolExecutionResponse,
    WorkflowExecutionResponse,
    GenericExecutionResponse,
]

ExecutionBatchResponse: TypeAlias = List[AnyExecutionResponse]


_REQUEST_MODEL_BY_RESOURCE_TYPE: Dict[str, Type[BaseModel]] = {
    "agent": AgentExecutionRequest,
    "tenantdb": TenantDbExecutionRequest,
    "knowledge": KnowledgeBaseExecutionRequest,
    "tool": ToolExecutionRequest,
    "workflow": WorkflowExecutionRequest,
}


def normalize_execution_request(
    *,
    resource_type: str,
    payload: Any,
) -> AnyExecutionRequest:
    """
    根据资源类型强制归一化执行请求，避免 Union 模型在弱约束输入下误判。
    """

    if isinstance(payload, BaseModel):
        raw_payload: Any = payload.model_dump(mode="json", by_alias=True, exclude_none=False)
    elif isinstance(payload, dict):
        raw_payload = payload
    else:
        raise TypeError("Execution payload must be a dict or a Pydantic model.")

    model_cls = _REQUEST_MODEL_BY_RESOURCE_TYPE.get(resource_type.strip().lower(), GenericExecutionRequest)
    return model_cls.model_validate(raw_payload)
