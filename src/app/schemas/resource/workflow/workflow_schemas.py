# src/app/schemas/resource/workflow/workflow_schemas.py

from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Dict, Any, List, Optional
from app.schemas.resource.resource_schemas import InstanceUpdate, InstanceRead
from app.engine.workflow import (
    NodeResultData, NodeState, StreamEvent, ParameterSchema
)
from app.schemas.common import SSEvent, ExecutionRequest, ExecutionResponse
from app.models.resource.workflow import Workflow, WorkflowNodeDef
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
    forms: List[Dict[str, Any]] # FormProperty 结构
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
                "forms": data.forms or [],
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
                "forms": data.get("forms") or [],
                "is_active": bool(data.get("is_active", True)),
            }
        return data

class WorkflowEvent(SSEvent):
    """Workflow 运行时产生的原子事件"""
    pass

class WorkflowExecutionRequest(ExecutionRequest):
    # 继承 generic inputs
    pass

class WorkflowExecutionResponseData(NodeResultData):
    trace_id: Optional[str] = Field(None)

class WorkflowExecutionResponse(ExecutionResponse):
    data: WorkflowExecutionResponseData
