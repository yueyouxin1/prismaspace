from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FolderBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_uuid: Optional[str] = Field(None, description="Parent folder UUID.")
    # Compatibility input for older clients.
    parent_id: Optional[int] = Field(None, description="Parent folder ID (deprecated).")


class AssetFolderCreate(FolderBase):
    pass


class AssetFolderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_uuid: Optional[str] = Field(None, description="Move folder to parent UUID.")
    # Compatibility input for older clients.
    parent_id: Optional[int] = Field(None, description="Move folder to parent ID (deprecated).")


class AssetFolderRead(BaseModel):
    id: int
    uuid: str
    name: str
    parent_uuid: Optional[str] = None
    # Compatibility field for old frontend clients.
    parent_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return data

        parent = getattr(data, "parent", None)
        return {
            "id": data.id,
            "uuid": data.uuid,
            "name": data.name,
            "parent_uuid": getattr(parent, "uuid", None),
            "parent_id": data.parent_id,
            "created_at": data.created_at,
        }


class AssetFolderTreeNodeRead(AssetFolderRead):
    children: List["AssetFolderTreeNodeRead"] = Field(default_factory=list)


AssetFolderTreeNodeRead.model_rebuild()
