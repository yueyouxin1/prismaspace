# tests/api/v1/asset/conftest.py

import pytest
from unittest.mock import MagicMock, AsyncMock
from app.core.storage.base import BaseStorageProvider, ObjectMetadata, UploadTicket

@pytest.fixture
def mock_storage_provider(monkeypatch):
    """
    Mock storage provider to bypass real OSS/S3 calls.
    Patches the factory to return this mock.
    """
    mock_provider = MagicMock(spec=BaseStorageProvider)
    mock_provider.name = "mock_provider"
    
    # Mock synchronous method
    mock_provider.generate_upload_ticket.return_value = UploadTicket(
        upload_url="http://mock-storage.com/upload",
        form_data={"key": "mock-key", "token": "mock-token"},
        provider="mock_provider",
        physical_key="mock/path/file.jpg"
    )
    
    # Mock synchronous method
    mock_provider.get_public_url.side_effect = lambda key: f"http://mock-storage.com/{key}"

    # Mock async method (Critical for Confirm step)
    # Default behavior: Success with generic metadata
    mock_provider.get_object_metadata = AsyncMock(return_value=ObjectMetadata(
        hash_str="mock_etag_hash",
        size=1024,
        content_type="image/jpeg"
    ))

    # Apply the patch to the factory function that AssetService uses
    # Note: We patch where it is IMPORTED or USED. 
    # Since AssetService calls `get_storage_provider()`, we patch that function.
    monkeypatch.setattr("app.services.asset.asset_service.get_storage_provider", lambda: mock_provider)
    
    return mock_provider