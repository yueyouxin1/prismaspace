# src/app/schemas/asset/folder_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime

class FolderBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: Optional[int] = None

class AssetFolderCreate(FolderBase):
    pass

class AssetFolderUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None

class AssetFolderRead(FolderBase):
    id: int
    uuid: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)