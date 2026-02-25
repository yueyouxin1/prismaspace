# src/app/schemas/resource/uiapp/uiapp_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional
from app.schemas.resource.resource_schemas import InstanceUpdate, InstanceRead
from .node import UiNode

# --- Page Schemas ---

class UiPageBase(BaseModel):
    page_uuid: str = Field(..., description="页面的唯一标识符")
    path: str = Field(..., description="路由路径")
    label: str = Field(..., description="页面名称")
    icon: Optional[str] = None
    display_order: int = 0
    config: Dict[str, Any] = Field(default_factory=dict)

class UiPageCreate(UiPageBase):
    data: List[UiNode] = Field(default_factory=list, description="页面组件树")

class UiPageUpdate(BaseModel):
    """单页更新请求"""
    path: Optional[str] = None
    label: Optional[str] = None
    icon: Optional[str] = None
    display_order: Optional[int] = None
    config: Optional[Dict[str, Any]] = None
    data: Optional[List[UiNode]] = None # 可选更新 DSL

class UiPageMeta(UiPageBase):
    """[Lightweight] 页面元数据，不含 DSL"""
    pass

class UiPageDetail(UiPageBase):
    """[Heavy] 页面详情，包含 DSL"""
    data: List[UiNode]
    model_config = ConfigDict(from_attributes=True)

# --- App Schemas ---

class UiAppSchema(BaseModel):
    """完整应用定义 (用于导入导出)"""
    global_config: Dict[str, Any] = Field(default_factory=dict)
    navigation: Optional[Dict[str, Any]] = None
    home_page_uuid: Optional[str] = None
    pages: List[UiPageCreate] = Field(default_factory=list)

class UiAppUpdate(InstanceUpdate):
    """
    App 级更新 (不包含具体 Page DSL 的修改，只修改全局配置或页面增删的元操作)
    """
    global_config: Optional[Dict[str, Any]] = None
    navigation: Optional[Dict[str, Any]] = None
    home_page_uuid: Optional[str] = None
    
class UiAppMetadataRead(InstanceRead):
    """
    [App Skeleton] 包含全局配置和页面列表(仅元数据)，不含页面 DSL。
    用于首屏加载。
    """
    global_config: Dict[str, Any]
    navigation: Optional[Dict[str, Any]]
    home_page_uuid: Optional[str]
    pages: List[UiPageMeta] # 仅元数据

    model_config = ConfigDict(from_attributes=True)