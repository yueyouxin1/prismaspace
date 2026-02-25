# tests/api/v1/asset/test_folders.py

import pytest
from httpx import AsyncClient
from fastapi import status
from app.models import Workspace

@pytest.mark.asyncio
async def test_folder_lifecycle(
    client: AsyncClient,
    registered_user_with_pro,  # UserContext
    auth_headers_factory,
    db_session
):
    """
    Test Create -> List -> Create Child -> Delete Flow
    """
    # Setup
    user_ctx = registered_user_with_pro
    headers = await auth_headers_factory(user_ctx)
    ws_uuid = user_ctx.personal_workspace.uuid

    # 1. Create Root Folder
    payload = {"name": "Designs", "parent_id": None}
    resp = await client.post(f"/api/v1/assets/folders?workspace_uuid={ws_uuid}", json=payload, headers=headers)
    assert resp.status_code == status.HTTP_200_OK
    root_folder = resp.json()["data"]
    assert root_folder["name"] == "Designs"
    assert root_folder["parent_id"] is None
    root_id = root_folder["id"]

    # 2. Create Child Folder
    payload_child = {"name": "Logos", "parent_id": root_id}
    resp = await client.post(f"/api/v1/assets/folders?workspace_uuid={ws_uuid}", json=payload_child, headers=headers)
    assert resp.status_code == status.HTTP_200_OK
    child_folder = resp.json()["data"]
    assert child_folder["parent_id"] == root_id
    child_id = child_folder["id"]

    # 3. List Folders (Root)
    resp = await client.get(f"/api/v1/assets/folders?workspace_uuid={ws_uuid}", headers=headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["data"]
    # Should see root folder
    assert any(f["id"] == root_id for f in data)
    # Should NOT see child folder (it's nested)
    assert not any(f["id"] == child_id for f in data)

    # 4. List Folders (Children of Root)
    resp = await client.get(f"/api/v1/assets/folders?workspace_uuid={ws_uuid}&parent_id={root_id}", headers=headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["id"] == child_id

    # 5. Try Delete Root (Should fail because it's not empty)
    resp = await client.delete(f"/api/v1/assets/folders/{root_folder['uuid']}", headers=headers)
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "not empty" in resp.json()["msg"]

    # 6. Delete Child (Success)
    resp = await client.delete(f"/api/v1/assets/folders/{child_folder['uuid']}", headers=headers)
    assert resp.status_code == status.HTTP_200_OK

    # 7. Delete Root (Now Success)
    resp = await client.delete(f"/api/v1/assets/folders/{root_folder['uuid']}", headers=headers)
    assert resp.status_code == status.HTTP_200_OK