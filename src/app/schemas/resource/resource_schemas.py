# app/schemas/resource/resource_schemas.py

from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Any, Optional, Dict
from datetime import datetime
from app.models import User
from app.models.resource import Resource, ResourceType, ResourceInstance
from app.schemas.project.project_schemas import CreatorInfo

AnyInstanceRead = Dict[str, Any]

class ResourceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="资源名称")
    description: Optional[str] = Field(None, description="资源描述")
    avatar: Optional[str] = Field(None, max_length=512, description="资源图标URL")

class ResourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="新资源的名称")
    resource_type: str = Field(..., description="要创建的资源类型 (e.g., 'tool', 'agent')")
    description: Optional[str] = Field(None)

class ResourceUpdate(BaseModel):
    """Schema for updating a Resource's metadata."""
    name: str = Field(..., min_length=1, max_length=100, description="资源的新名称")
    description: Optional[str] = Field(None, description="资源的新描述")
    avatar: Optional[str] = Field(None, max_length=512, description="资源的新图标URL")

class ResourceRead(BaseModel):
    uuid: str
    name: str
    description: Optional[str] = None
    avatar: Optional[str] = None
    resource_type: str
    workspace_instance_uuid: Optional[str]
    latest_published_instance_uuid: Optional[str]
    creator: CreatorInfo
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
    
    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if not isinstance(data, Resource):
            return data
        return {
            "uuid": data.uuid,
            "name": data.name,
            "description": data.description,
            "avatar": data.avatar,
            "resource_type": data.resource_type.name if data.resource_type else "unknown",
            "workspace_instance_uuid": data.workspace_instance.uuid if data.workspace_instance else None,
            "latest_published_instance_uuid": data.latest_published_instance.uuid if data.latest_published_instance else None,
            "creator": CreatorInfo.model_validate(data.creator) if data.creator else None,
            "created_at": data.created_at,
            "updated_at": data.updated_at,
        }

class ResourceDetailRead(ResourceRead):
    """
    用于 GET /resources/{uuid} 的聚合响应模型。
    它精确地服务于“进入编辑/详情视图”的用例。
    """
    workspace_instance: Optional[AnyInstanceRead] = None
    
    # 我们仍然需要 latest_published_instance 的 UUID，以便前端知道是否存在线上版本
    # 但我们不再需要它的完整内容
    latest_published_instance_uuid: Optional[str] = Field(None)

    # 隐藏父类中重复的字段
    workspace_instance_uuid: Optional[str] = Field(None, exclude=True)

    model_config = ConfigDict(from_attributes=True)

class InstanceUpdate(BaseModel):
    visibility: Optional[str] = Field("private")
    model_config = ConfigDict(from_attributes=True)

class InstancePublish(BaseModel):
    """Schema for publishing a new version of a resource instance."""
    version_tag: str = Field(..., description="The new version tag (e.g., '1.0.0'). Must be unique for the resource.")
    version_notes: Optional[str] = Field(None, description="Notes describing the changes in this version.")
    
class InstanceRead(BaseModel):
    uuid: str
    version_tag: str
    status: str
    created_at: datetime
    creator: CreatorInfo
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        # 这个验证器将在所有继承它的子类中生效
        if not isinstance(data, ResourceInstance):
            return data
        return {
            "uuid": data.uuid,
            "version_tag": data.version_tag,
            "status": data.status.value,
            "created_at": data.created_at,
            "creator": CreatorInfo.model_validate(data.creator) if data.creator else None,
        }

class ResourceDependencyRead(BaseModel):
    resource_uuid: str
    instance_uuid: str
    alias: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
