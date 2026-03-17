# src/app/schemas/resource/workflow/workflow_schemas.py

from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Dict, Any, List, Optional, Literal
from app.schemas.resource.resource_schemas import InstanceUpdate, InstanceRead
from app.schemas.resource.runtime_checkpoint import RuntimeCheckpointEnvelopeRead
from app.engine.workflow import (
    NodeResultData, ParameterSchema
)
from app.schemas.common import SSEvent, ExecutionRequest, ExecutionResponse
from app.models.resource.workflow import (
    Workflow,
    WorkflowCheckpointReason,
    WorkflowExecutionCheckpoint,
    WorkflowExecutionEvent,
    WorkflowNodeDef,
    WorkflowNodeExecution,
)
from app.schemas.project.project_schemas import CreatorInfo

class WorkflowSchema(BaseModel):
    graph: Dict[str, Any] = Field(..., description="工作流 DSL")
    inputs_schema: List[ParameterSchema] = Field(default_factory=list)
    outputs_schema: List[ParameterSchema] = Field(default_factory=list)
    is_stream: bool = Field(default=False)

class WorkflowUpdate(WorkflowSchema, InstanceUpdate):
    # 更新时 graph 是可选的，但如果提供了 graph，服务层会重新计算 schema
    graph: Optional[Dict[str, Any]] = None
    # schema 和 is_stream 通常是计算出来的，不建议直接手动 update，除非是特殊的 override 逻辑
    # 这里我们允许更新，但 Service 层会覆盖它们
    pass

class WorkflowRead(InstanceRead, WorkflowSchema):
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if isinstance(data, Workflow):
            status_value = data.status.value if hasattr(data.status, "value") else str(data.status)
            return {
                "uuid": data.uuid,
                "name": data.name,
                "description": data.description,
                "version_tag": data.version_tag,
                "status": status_value,
                "created_at": data.created_at,
                "updated_at": getattr(data, "updated_at", None) or data.created_at,
                "creator": CreatorInfo.model_validate(data.creator) if data.creator else None,
                "graph": data.graph or {},
                "inputs_schema": data.inputs_schema or [],
                "outputs_schema": data.outputs_schema or [],
                "is_stream": bool(data.is_stream),
            }
        return data

class WorkflowNodeDefRead(BaseModel):
    id: int
    node_uid: str
    category: str
    label: str
    icon: Optional[str]
    description: Optional[str]
    display_order: int
    node: Dict[str, Any] # WorkflowNode 结构
    is_active: bool
    
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if isinstance(data, WorkflowNodeDef):
            node_payload = data.data or {}
            return {
                "id": data.id,
                "node_uid": data.registry_id,
                "category": data.category,
                "label": node_payload.get("name") or data.registry_id,
                "icon": data.icon,
                "description": node_payload.get("description"),
                "display_order": data.display_order,
                "node": node_payload,
                "is_active": data.is_active,
            }
        if isinstance(data, dict) and "registry_id" in data and "node_uid" not in data:
            node_payload = data.get("data") or {}
            return {
                "id": data.get("id"),
                "node_uid": data.get("registry_id"),
                "category": data.get("category"),
                "label": node_payload.get("name") or data.get("registry_id"),
                "icon": data.get("icon"),
                "description": node_payload.get("description"),
                "display_order": data.get("display_order", 0),
                "node": node_payload,
                "is_active": bool(data.get("is_active", True)),
            }
        return data

class WorkflowEvent(SSEvent):
    """Workflow 运行时产生的原子事件"""
    pass


class WorkflowEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence_no: int
    event_type: str
    payload: Dict[str, Any]
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def pre_process_event(cls, data: Any) -> Any:
        if isinstance(data, WorkflowExecutionEvent):
            event_type = data.event_type.value if hasattr(data.event_type, "value") else str(data.event_type)
            return {
                "sequence_no": data.sequence_no,
                "event_type": event_type,
                "payload": data.payload or {},
                "created_at": data.created_at,
            }
        return data

class WorkflowExecutionRequest(ExecutionRequest):
    thread_id: Optional[str] = Field(default=None, description="逻辑执行线程 ID。")
    parent_run_id: Optional[str] = Field(default=None, description="上游运行 ID，用于 retry/regenerate 谱系。")
    resume_from_run_id: Optional[str] = Field(default=None, description="从指定 run_id 的最新 checkpoint 恢复。")


class WorkflowInterruptRead(BaseModel):
    id: Optional[str] = None
    node_id: str
    reason: str
    message: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class WorkflowExecutionResponseData(NodeResultData):
    trace_id: Optional[str] = Field(None)
    run_id: Optional[str] = Field(None)
    thread_id: Optional[str] = Field(None)
    outcome: Optional[Literal["success", "interrupt", "cancelled"]] = None
    interrupt: Optional[WorkflowInterruptRead] = None

class WorkflowExecutionResponse(ExecutionResponse):
    data: WorkflowExecutionResponseData


class WorkflowRunNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    node_name: str
    node_type: str
    attempt: int
    status: str
    input: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    activated_port: Optional[str] = None
    executed_time: float = 0.0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def pre_process_node_execution(cls, data: Any) -> Any:
        if isinstance(data, WorkflowNodeExecution):
            return {
                "node_id": data.node_id,
                "node_name": data.node_name,
                "node_type": data.node_type,
                "attempt": data.attempt,
                "status": data.status,
                "input": data.input,
                "result": data.result,
                "error_message": data.error_message,
                "activated_port": data.activated_port,
                "executed_time": data.executed_time,
                "started_at": data.started_at,
                "finished_at": data.finished_at,
            }
        return data


class WorkflowCheckpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step_index: int
    reason: str
    node_id: Optional[str] = None
    canonical: Optional[RuntimeCheckpointEnvelopeRead] = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def pre_process_checkpoint(cls, data: Any) -> Any:
        if isinstance(data, WorkflowExecutionCheckpoint):
            reason = data.reason.value if hasattr(data.reason, "value") else str(data.reason)
            return {
                "id": data.id,
                "step_index": data.step_index,
                "reason": reason,
                "node_id": data.node_id,
                "created_at": data.created_at,
            }
        return data


class WorkflowRunRead(BaseModel):
    run_id: str
    thread_id: str
    parent_run_id: Optional[str] = None
    status: str
    trace_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    workflow_instance_uuid: str
    workflow_name: str
    latest_checkpoint: Optional[WorkflowCheckpointRead] = None
    node_executions: List[WorkflowRunNodeRead] = Field(default_factory=list)
    can_resume: bool = False


class WorkflowRunSummaryRead(BaseModel):
    run_id: str
    thread_id: str
    parent_run_id: Optional[str] = None
    status: str
    trace_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    latest_checkpoint: Optional[WorkflowCheckpointRead] = None
