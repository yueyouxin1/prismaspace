# app/schemas/workspace/workspace_schemas.py

from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, Literal, Any
from app.models.workspace import Workspace, WorkspaceStatus

# ==============================================================================
# 1. 基础与共享模型
# ==============================================================================

class WorkspaceBase(BaseModel):
    """工作空间的基础字段，用于创建和更新。"""
    name: str = Field(..., min_length=1, max_length=255, description="工作空间名称")
    avatar: Optional[str] = Field(None, max_length=512, description="工作空间头像URL")

class OwnerInfo(BaseModel):
    """
    [设计良好] 这个子模型是清晰表达所有权的最佳方式。
    它只提供必要的标识信息，而不是完整的UserRead或TeamRead。
    """
    uuid: str # 使用 uuid 替代 id
    type: Literal["user", "team"]
    name: str
    avatar: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

# ==============================================================================
# 2. API 输入模型 (Input Schemas)
# ==============================================================================

class WorkspaceCreate(WorkspaceBase):
    """
    [关键修改] 创建团队工作空间时，应接收 team_uuid。
    """
    owner_team_uuid: str = Field(..., description="此工作空间所属团队的UUID")

class WorkspaceUpdate(WorkspaceBase):
    """用于更新工作空间的Schema。保持不变。"""
    pass

# ==============================================================================
# 3. API 输出模型 (Output Schemas)
# ==============================================================================

class WorkspaceRead(BaseModel):
    uuid: str = Field(..., description="工作空间的全局唯一标识符")
    name: str
    avatar: Optional[str] = None
    status: str = Field(..., description="工作空间状态 (e.g., 'active', 'archived')")
    owner: OwnerInfo = Field(..., description="工作空间的所有者信息 (个人或团队)")
    
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if not isinstance(data, Workspace):
            return data

        owner_info_data = None
        if data.user_owner:
            owner_info_data = {
                "uuid": data.user_owner.uuid,
                "type": "user",
                "name": data.user_owner.nick_name or data.user_owner.email,
                "avatar": data.user_owner.avatar
            }
        elif data.team:
            owner_info_data = {
                "uuid": data.team.uuid,
                "type": "team",
                "name": data.team.name,
                "avatar": data.team.avatar
            }
        
        return {
            "uuid": data.uuid,
            "name": data.name,
            "avatar": data.avatar,
            "status": data.status.value if isinstance(data.status, WorkspaceStatus) else data.status,
            "owner": owner_info_data
        }