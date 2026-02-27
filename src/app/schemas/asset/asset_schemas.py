from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.asset import AssetStatus, AssetType, IntelligenceStatus


class AssetCreate(BaseModel):
    """Create an upload ticket for client-side direct upload."""

    folder_uuid: Optional[str] = Field(None, description="Target folder UUID.")
    # Compatibility input for older clients.
    folder_id: Optional[int] = Field(None, description="Target folder ID (deprecated).")
    filename: str = Field(..., min_length=1, max_length=255, description="Original filename.")
    size_bytes: int = Field(..., gt=0, description="File size in bytes.")
    mime_type: str = Field(..., min_length=1, max_length=100, description="MIME type.")


class AssetUploadTicket(BaseModel):
    """Direct upload ticket returned to frontend."""

    asset_uuid: str = Field(..., description="Generated asset UUID for confirm step.")
    upload_url: str = Field(..., description="Storage host URL.")
    form_data: Dict[str, Any] = Field(..., description="Multipart form fields.")
    provider: str = Field(..., description="Storage provider id.")
    upload_key: str = Field(..., description="Physical storage key.")
    folder_uuid: Optional[str] = Field(None, description="Resolved target folder UUID.")


class AssetConfirm(BaseModel):
    """Confirm upload completion and create logical asset."""

    workspace_uuid: str = Field(..., description="Workspace UUID.")
    asset_uuid: str = Field(..., description="Asset UUID from ticket.")
    upload_key: str = Field(..., description="Uploaded physical key.")
    folder_uuid: Optional[str] = Field(None, description="Target folder UUID.")
    # Compatibility input for older clients.
    folder_id: Optional[int] = Field(None, description="Target folder ID (deprecated).")
    name: Optional[str] = Field(None, max_length=255, description="Display name.")
    force_ai_processing: Optional[bool] = Field(None, description="Override workspace AI processing strategy.")


class AssetRead(BaseModel):
    uuid: str
    name: str = Field(..., max_length=255, description="Display name.")
    url: str
    size: int
    type: AssetType
    status: AssetStatus
    created_at: datetime
    updated_at: datetime
    mime_type: Optional[str]
    folder_uuid: Optional[str] = Field(None, description="Parent folder UUID.")
    # Compatibility field for old frontend clients.
    folder_id: Optional[int] = Field(None, description="Parent folder ID (deprecated).")
    ai_status: Optional[IntelligenceStatus] = Field(None)
    ai_meta: Optional[Dict[str, Any]] = Field(None)

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return data

        folder = getattr(data, "folder", None)
        intelligence = getattr(data, "intelligence", None)

        return {
            "uuid": data.uuid,
            "name": data.name,
            "url": data.url,
            "size": data.size,
            "type": data.type,
            "status": data.status,
            "created_at": data.created_at,
            "updated_at": data.updated_at,
            "mime_type": data.mime_type,
            "folder_uuid": getattr(folder, "uuid", None),
            "folder_id": data.folder_id,
            "ai_status": getattr(intelligence, "status", None),
            "ai_meta": getattr(intelligence, "meta", None),
        }


class PaginatedAssetsResponse(BaseModel):
    items: List[AssetRead]
    total: int
    page: int
    limit: int


class AssetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    folder_uuid: Optional[str] = Field(None, description="Move asset to target folder UUID.")
    # Compatibility input for older clients.
    folder_id: Optional[int] = Field(None, description="Move asset by folder ID (deprecated).")
