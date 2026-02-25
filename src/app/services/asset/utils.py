import os
import time
from typing import Literal
from datetime import datetime
from app.models import AssetType

def detect_asset_type(mime_type: str) -> AssetType:
    """根据 MIME 类型权威判定资产类型"""
    if mime_type.startswith("image/"):
        return AssetType.IMAGE
    if mime_type.startswith("video/"):
        return AssetType.VIDEO
    if mime_type.startswith("audio/"):
        return AssetType.AUDIO
    if mime_type in [
        "application/pdf", "text/plain", "text/csv", "text/markdown",
        "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]:
        return AssetType.DOCUMENT
    return AssetType.OTHER

def generate_storage_key(
    owner_type: Literal["user", "team"], 
    owner_id: int, 
    file_uuid: str, 
    original_filename: str
) -> str:
    """
    生成物理存储路径 (Key)。
    Format: {owner_type}s/{owner_id}/assets/{yyyy}/{mm}/{uuid}{ext}
    Example: teams/101/assets/2023/10/550e8400-e29b-41d4-a716-446655440000.png
    """
    now = datetime.now()
    year = now.strftime("%Y")
    month = now.strftime("%m")
    
    _, ext = os.path.splitext(original_filename)
    if not ext:
        ext = ""
    else:
        ext = ext.lower()

    return f"{owner_type}s/{owner_id}/assets/{year}/{month}/{file_uuid}{ext}"