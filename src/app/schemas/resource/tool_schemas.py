# app/schemas/resource/tool_schemas.py

from pydantic import BaseModel, Field, ConfigDict, model_validator, HttpUrl
from typing import Optional, Dict, Any, List 
from app.models.resource.tool import Tool
from .resource_schemas import InstanceUpdate, InstanceRead
from app.schemas.project.project_schemas import CreatorInfo
from app.engine.schemas.parameter_schema import ParameterSchema
from app.schemas.common import ExecutionRequest, ExecutionResponse

class ToolSchema(BaseModel):
    """Tool特有的、可被编辑的实现细节。"""
    url: Optional[HttpUrl] = Field(None, description="工具的API端点URL")
    method: str = Field("GET", description="HTTP请求方法 (GET, POST, etc.)")
    inputs_schema: List[ParameterSchema] = Field(default_factory=dict, description="输入参数的JSON Schema")
    outputs_schema: List[ParameterSchema] = Field(default_factory=dict, description="输出结果的JSON Schema")
    llm_function_schema: Optional[Dict[str, Any]] = Field(None, description="提供给大语言模型的Function Calling Schema")

class ToolUpdate(ToolSchema, InstanceUpdate):
    """用于更新一个Tool实例的Schema。"""
    pass

class ToolRead(InstanceRead, ToolSchema):
    model_config = ConfigDict(from_attributes=True)
    
    # [关键修复] 添加 model_validator 来处理复杂的ORM对象转换
    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if not isinstance(data, Tool):
            return data
        validated_inputs_schema = [ParameterSchema.model_validate(p) for p in data.inputs_schema or []]
        validated_outputs_schema = [ParameterSchema.model_validate(p) for p in data.outputs_schema or []]
        instance_dict = {
            # 从 InstanceRead 继承的字段
            "uuid": data.uuid,
            "version_tag": data.version_tag,
            "status": data.status.value,
            "created_at": data.created_at,
            "creator": data.creator, # 直接传递User对象，让CreatorInfo.model_validate处理
            
            # 从 ToolSchema 继承的字段
            "url": str(data.url) if data.url is not None else None, # 确保是字符串
            "method": data.method,
            "inputs_schema": validated_inputs_schema,
            "outputs_schema": validated_outputs_schema,
            "llm_function_schema": data.llm_function_schema
        }
        # CreatorInfo 还需要自己的验证器
        if instance_dict["creator"]:
                instance_dict["creator"] = CreatorInfo.model_validate(instance_dict["creator"])
        return instance_dict

class ToolExecutionMeta(BaseModel):
    return_raw_response: bool = Field(False, description="[仅workspace版本有效] 是否返回原始响应")

class ToolExecutionRequest(ExecutionRequest):
    meta: Optional[ToolExecutionMeta] = Field(None, description="Execution-specific options, not part of the resource's business inputs.")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Tool运行时参数")

class ToolExecutionResponse(ExecutionResponse):
    """The structured response for a successful Tool execution."""
    success: bool = True
    data: Dict[str, Any] = Field(..., description="The shaped output data from the tool, matching its outputs_schema.")