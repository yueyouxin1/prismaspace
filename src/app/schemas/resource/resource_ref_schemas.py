# src/app/schemas/resource/resource_ref_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime

# --- 基础引用信息 ---
class ReferenceBase(BaseModel):
    # 局部锚点 (Workflow Node ID 或 UiApp Component ID)
    source_node_uuid: Optional[str] = Field(None, description="源资源内部的引用节点/组件ID")
    alias: Optional[str] = Field(None, description="别名")
    options: Optional[Dict[str, Any]] = Field(None, description="其他配置")

# --- 创建请求 ---
class ReferenceCreate(ReferenceBase):
    target_instance_uuid: str = Field(..., description="被引用目标的实例UUID")

# --- 响应信息 ---
class ReferenceRead(ReferenceBase):
    id: int
    source_instance_uuid: str = Field(..., alias="source_instance.uuid")
    
    # 展开目标信息，方便前端展示
    target_instance_uuid: str = Field(..., alias="target_instance.uuid")
    target_resource_name: str = Field(..., alias="target_resource.name")
    target_resource_type: str = Field(..., alias="target_resource.resource_type.name")
    target_version_tag: str = Field(..., alias="target_instance.version_tag")
    
    model_config = ConfigDict(from_attributes=True)

class BatchSyncReferences(BaseModel):
    """用于全量同步依赖关系的请求体"""
    references: list[ReferenceCreate]