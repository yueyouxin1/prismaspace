# src/app/schemas/project/project_resource_schemas.py

from datetime import datetime
from typing import Any, Optional, Dict
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.models.resource import ProjectResourceRef, Resource
from app.schemas.project.project_schemas import CreatorInfo


class ProjectResourceReferenceCreate(BaseModel):
    resource_uuid: str = Field(..., description="被引用资源UUID")
    alias: Optional[str] = Field(None, description="项目内别名")
    options: Optional[Dict[str, Any]] = Field(None, description="项目级引用配置")


class ProjectResourceReferenceRead(BaseModel):
    id: int
    resource_uuid: str
    resource_name: str
    resource_type: str
    workspace_instance_uuid: Optional[str]
    latest_published_instance_uuid: Optional[str]
    creator: Optional[CreatorInfo]
    alias: Optional[str]
    options: Optional[Dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if not isinstance(data, ProjectResourceRef):
            return data

        resource: Resource | None = data.resource
        return {
            "id": data.id,
            "resource_uuid": resource.uuid if resource else None,
            "resource_name": resource.name if resource else None,
            "resource_type": resource.resource_type.name if resource and resource.resource_type else "unknown",
            "workspace_instance_uuid": resource.workspace_instance.uuid if resource and resource.workspace_instance else None,
            "latest_published_instance_uuid": resource.latest_published_instance.uuid if resource and resource.latest_published_instance else None,
            "creator": CreatorInfo.model_validate(resource.creator) if resource and resource.creator else None,
            "alias": data.alias,
            "options": data.options,
            "created_at": data.created_at,
        }
