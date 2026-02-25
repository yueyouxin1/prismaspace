# src/app/schemas/resource/resource_type_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, List, Any

class ResourceTypeBase(BaseModel):
    """ResourceType的基础模型，包含所有可写字段。"""
    name: str = Field(..., pattern=r'^[a-z_]+$', description="类型的唯一标识符 (e.g., 'uiapp', 'tool')")
    label: str = Field(..., description="UI友好名称 (e.g., '可视化应用')")
    description: Optional[str] = None
    is_application: bool = Field(False, description="是否可作为项目的主应用/入口资源")
    meta_policy: Optional[Dict[str, Any]] = Field(None, description="定义该类型资源行为的元策略")
    allowed_visibilities: List[str] = Field(default=["private", "workspace", "public"], description="允许的可见性选项")
    allowed_channels: List[str] = Field(default=["default", "marketplace", "api"], description="允许的发布渠道")
    requires_approval: bool = Field(False, description="发布此类型的资源是否需要平台审核")

class ResourceTypeCreate(ResourceTypeBase):
    """用于创建新资源类型的Schema。"""
    pass

class ResourceTypeUpdate(BaseModel):
    """用于更新资源类型的Schema，所有字段都是可选的。"""
    label: Optional[str] = None
    description: Optional[str] = None
    is_application: Optional[bool] = None
    meta_policy: Optional[Dict[str, Any]] = None
    allowed_visibilities: Optional[List[str]] = None
    allowed_channels: Optional[List[str]] = None
    requires_approval: Optional[bool] = None

class ResourceTypeRead(ResourceTypeBase):
    """用于从API读取和返回资源类型数据的Schema。"""
    id: int

    model_config = ConfigDict(from_attributes=True)