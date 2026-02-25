# src/app/schemas/permission/permission_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Any
from app.models import ActionPermissionType

# --- Recursive Node for Creating ---
class PermissionCreateNode(BaseModel):
    """A node for creating a permission, allowing for nested children."""
    name: str = Field(..., description="The unique, dot-separated identifier (e.g., 'project:write:settings')")
    label: str = Field(..., description="The human-readable name for UIs (e.g., '更新项目设置')")
    description: Optional[str] = Field(None, description="A detailed description of what the permission allows.")
    type: ActionPermissionType = Field(..., description="The type of the permission (ABILITY, API, PAGE, etc.)")
    is_assignable: bool = Field(True, description="Whether this permission can be assigned to custom roles by team admins.")
    children: List['PermissionCreateNode'] = Field(default_factory=list)

class PermissionCreate(PermissionCreateNode):
    """The root schema for creating a new permission tree."""
    parent_name: Optional[str] = Field(None, description="The 'name' of the parent permission to attach this new tree to. If null, it's a new root.")

# --- Recursive Node for Reading ---
class PermissionReadNode(BaseModel):
    """A node for reading a permission, including its children."""
    id: int
    parent_id: Optional[int] = None
    name: str
    label: str
    description: Optional[str] = None
    type: ActionPermissionType
    is_assignable: bool
    children: List['PermissionReadNode'] = Field(default_factory=list) # 确保默认为空列表
    
    model_config = ConfigDict(from_attributes=True)

# Update forward references
PermissionCreateNode.model_rebuild()
PermissionReadNode.model_rebuild()


class PermissionUpdate(BaseModel):
    """Schema for updating an existing permission. Name is not updatable."""
    label: Optional[str] = None
    description: Optional[str] = None
    type: Optional[ActionPermissionType] = None
    is_assignable: Optional[bool] = None