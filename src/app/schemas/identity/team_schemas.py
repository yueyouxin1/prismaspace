# app/schemas/identity/team_schemas.py

from pydantic import BaseModel, Field, ConfigDict, computed_field, EmailStr
from typing import Optional, List, Literal
from app.models.identity import TeamMember
from app.schemas.identity.user_schemas import UserRead
from app.schemas.permission.role_schemas import RoleSummaryRead

class TeamBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="团队名称")
    avatar: Optional[str] = Field(None, max_length=512, description="团队头像URL")

class TeamCreate(TeamBase):
    pass

class TeamUpdate(TeamBase):
    pass

class TeamRead(TeamBase):
    uuid: str = Field(..., description="团队的全局唯一标识符")
    # owner_uuid: str = Field(..., description="团队所有者的用户UUID") # 移除，通过成员列表接口获取
    
    model_config = ConfigDict(from_attributes=True)

class TeamMemberRead(BaseModel):
    uuid: str = Field(..., description="成员关系的唯一标识符")
    user: UserRead = Field(..., description="成员的用户信息")
    role: RoleSummaryRead = Field(..., description="成员的角色信息")

    model_config = ConfigDict(from_attributes=True)

class InvitationCreate(BaseModel):
    target_identifier: EmailStr = Field(..., description="被邀请者的邮箱")
    role_name: str = Field(..., description="要分配给被邀请者的角色")

class InvitationAccept(BaseModel):
    token: str = Field(..., description="邀请链接中的唯一令牌")