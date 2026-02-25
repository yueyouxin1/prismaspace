# src/app/schemas/permission/role_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from app.models.permission import RoleType

# --- RolePermissionRead remains the same ---
class RolePermissionRead(BaseModel):
    """用于在角色详情中展示权限的摘要信息。"""
    name: str
    label: str
    description: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class RoleSummaryRead(BaseModel):
    """
    用于在其他模型中嵌套展示角色摘要信息的Schema。
    关键：它不包含 'permissions' 字段。
    """
    uuid: str
    name: str
    label: str
    description: Optional[str] = None
    role_type: RoleType
    
    model_config = ConfigDict(from_attributes=True, extra="ignore")

# --- RoleRead is enhanced ---
class RoleRead(BaseModel):
    """用于从API读取和返回角色数据的Schema (完整版)。"""
    uuid: str
    name: str
    label: str
    description: Optional[str] = None
    
    # [ENHANCED] Expose type and hierarchy for clarity
    role_type: RoleType
    parent_name: Optional[str] = Field(None, alias="parent.name")

    # This shows the FINAL, calculated set of permissions for the role
    permissions: List[RolePermissionRead] = []
    
    model_config = ConfigDict(from_attributes=True, extra="ignore")

# --- RoleCreate is enhanced ---
class RoleCreate(BaseModel):
    """用于创建新角色的Schema，完全支持继承和类型定义。"""
    name: str = Field(..., description="角色的唯一标识符 (e.g., 'developer', 'qa_tester')")
    label: str = Field(..., description="角色的显示名称 (e.g., '开发者')")
    description: Optional[str] = Field(None)
    
    # [NEW & CRITICAL] Explicitly define the role's purpose.
    # For custom team roles, this will be the default. For system roles, it must be provided.
    role_type: RoleType = Field(RoleType.CUSTOM_TEAM, description="The type of the role, determining its scope and behavior.")
    
    # [NEW & CRITICAL] Explicitly define the inheritance source
    parent_name: Optional[str] = Field(None, description="The name of the parent role from which to inherit permissions.")
    
    # [CLARIFIED] This list contains ONLY permissions directly assigned to this role.
    permissions: List[str] = Field(
        default_factory=list, 
        description="A list of permission names to grant DIRECTLY to this role. Inherited permissions are calculated automatically."
    )

# --- RoleUpdate is enhanced ---
class RoleUpdate(BaseModel):
    """
    用于更新角色的Schema。
    注意：更新 'permissions' 会触发对该角色及其所有子角色的权限重新计算。
    """
    label: Optional[str] = None
    description: Optional[str] = None

    # Updating permissions will trigger a recalculation for this role and its children.
    # For simplicity, reparenting is not supported via this simple update endpoint.
    permissions: Optional[List[str]] = Field(None, description="The complete new set of DIRECT permissions. This will overwrite existing direct permissions and trigger a cascade recalculation.")