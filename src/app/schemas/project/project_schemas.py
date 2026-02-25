# app/schemas/project/project_schemas.py

from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Any, Optional, Literal
from datetime import datetime
from app.models.workspace import Project, ProjectVisibility, ProjectStatus

MainApplicationType = Literal["uiapp", "agent"]

class CreatorInfo(BaseModel):
    """用于在响应中展示创建者的摘要信息。"""
    uuid: str
    nick_name: Optional[str] = None
    avatar: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class ProjectBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="项目名称")
    description: Optional[str] = Field(None, description="项目描述")
    avatar: Optional[str] = Field(None, max_length=512, description="项目图标URL")
    visibility: ProjectVisibility = Field(ProjectVisibility.PRIVATE, description="项目可见性")

class ProjectCreate(ProjectBase):
    main_application_type: MainApplicationType = Field(
        ...,
        description="主应用类型，仅允许 uiapp 或 agent"
    )

class ProjectUpdate(ProjectBase):
    pass

class ProjectRead(BaseModel):
    # 1. 定义我们最终想要的字段和类型
    uuid: str
    name: str
    description: Optional[str] = None
    avatar: Optional[str] = None
    status: str
    visibility: str
    creator: CreatorInfo
    main_resource_uuid: Optional[str] = None
    main_resource_name: Optional[str] = None
    main_application_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # 2. 配置 from_attributes=True
    model_config = ConfigDict(from_attributes=True)
    
    # 3. 使用 model_validator 来处理复杂的转换
    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if not isinstance(data, Project):
            return data
        return {
            "uuid": data.uuid,
            "name": data.name,
            "description": data.description,
            "avatar": data.avatar,
            "status": data.status.value if isinstance(data.status, ProjectStatus) else data.status,
            "visibility": data.visibility.value if isinstance(data.visibility, ProjectVisibility) else data.visibility,
            "creator": CreatorInfo.model_validate(data.creator) if data.creator else None,
            "main_resource_uuid": data.main_resource.uuid if data.main_resource else None,
            "main_resource_name": data.main_resource.name if data.main_resource else None,
            "main_application_type": (
                data.main_resource.resource_type.name
                if data.main_resource and data.main_resource.resource_type
                else None
            ),
            "created_at": data.created_at,
            "updated_at": data.updated_at,
        }
