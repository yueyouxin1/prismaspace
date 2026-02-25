# src/app/schemas/asset/asset_schemas.py

from pydantic import BaseModel, Field, ConfigDict, HttpUrl
from typing import Optional, Dict, Any, List
from datetime import datetime
from app.models.asset import AssetType, AssetStatus, IntelligenceStatus

# --- Create (Reservation Step) ---
class AssetCreate(BaseModel):
    """请求上传凭证"""
    folder_id: Optional[int] = Field(None, description="目标文件夹ID")
    filename: str = Field(..., description="原始文件名")
    size_bytes: int = Field(..., gt=0, description="文件大小")
    mime_type: str = Field(..., description="MIME类型")

# --- Upload Response (Ticket) ---
class AssetUploadTicket(BaseModel):
    """
    Returned to frontend to perform the direct upload.
    """
    asset_uuid: str = Field(..., description="The UUID of the newly created PENDING asset")
    upload_url: str = Field(..., description="The host URL to POST the file to")
    form_data: Dict[str, Any] = Field(..., description="Key-value pairs to include in the multipart form data")
    provider: str = Field(..., description="Storage provider identifier (e.g., 'aliyun_oss')")
    upload_key: str = Field(..., description="物理上传路径")

# --- Confirm (Commit Step) ---
class AssetConfirm(BaseModel):
    """确认上传的参数"""
    workspace_uuid: str = Field(..., description="所属工作空间UUID")
    asset_uuid: str = Field(..., description="Ticket阶段生成的资产UUID")
    upload_key: str = Field(..., description="实际上传的物理路径Key")
    
    name: Optional[str] = None
    force_ai_processing: Optional[bool] = None 

# --- Read ---
class AssetRead(BaseModel):
    uuid: str
    name: str = Field(..., max_length=255, description="File display name")
    url: str
    size: int
    type: AssetType
    status: AssetStatus
    created_at: datetime
    updated_at: datetime
    mime_type: Optional[str]
    folder_id: Optional[int] = Field(None, description="Parent folder ID")
    # 智能信息展开
    ai_status: Optional[IntelligenceStatus] = Field(None)
    ai_meta: Optional[Dict[str, Any]] = Field(None)

    model_config = ConfigDict(from_attributes=True)

class AssetUpdate(BaseModel):
    name: Optional[str] = None
    folder_id: Optional[int] = None
    # Meta updates are typically handled by internal workers, not user API directly