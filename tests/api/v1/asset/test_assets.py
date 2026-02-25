import pytest
from httpx import AsyncClient
from fastapi import status
from app.models.asset import AssetType, IntelligenceStatus
from app.core.storage.base import ObjectMetadata

@pytest.mark.asyncio
async def test_asset_upload_flow(
    client: AsyncClient,
    registered_user_with_pro,
    auth_headers_factory,
    mock_storage_provider
):
    # 此测试无需修改，默认 mock 返回 image/jpeg 符合预期
    user_ctx = registered_user_with_pro
    headers = await auth_headers_factory(user_ctx)
    ws_uuid = user_ctx.personal_workspace.uuid

    ticket_payload = {
        "filename": "vacation.jpg",
        "size_bytes": 102400,
        "mime_type": "image/jpeg"
    }
    
    resp = await client.post(
        f"/api/v1/assets/upload/ticket?workspace_uuid={ws_uuid}", 
        json=ticket_payload, 
        headers=headers
    )
    assert resp.status_code == status.HTTP_200_OK
    ticket_data = resp.json()["data"]
    
    confirm_payload = {
        "workspace_uuid": ws_uuid,
        "asset_uuid": ticket_data["asset_uuid"],
        "upload_key": ticket_data["upload_key"],
        "name": "My Vacation Photo"
    }

    resp = await client.post(
        "/api/v1/assets/upload/confirm",
        json=confirm_payload,
        headers=headers
    )
    
    assert resp.status_code == status.HTTP_200_OK
    asset_data = resp.json()["data"]
    
    assert asset_data["type"] == AssetType.IMAGE
    assert asset_data["ai_status"] == IntelligenceStatus.PENDING


@pytest.mark.asyncio
async def test_asset_management(
    client: AsyncClient,
    registered_user_with_pro,
    auth_headers_factory,
    mock_storage_provider
):
    """
    Test List (Filter) -> Update (Rename/Move) -> Delete
    """
    user_ctx = registered_user_with_pro
    headers = await auth_headers_factory(user_ctx)
    ws_uuid = user_ctx.personal_workspace.uuid
    
    async def create_asset(filename, mime_type):
        # 1. Ticket
        t_resp = await client.post(
            f"/api/v1/assets/upload/ticket?workspace_uuid={ws_uuid}", 
            json={"filename": filename, "size_bytes": 100, "mime_type": mime_type}, 
            headers=headers
        )
        assert t_resp.status_code == 200
        ticket = t_resp.json()["data"]
        
        # 2. [FIX] Configure Mock to return corresponding mime_type
        # 这确保了 Service.confirm_upload 中的 detect_asset_type 能正确工作
        mock_storage_provider.get_object_metadata.return_value = ObjectMetadata(
            hash_str=f"hash_{filename}",
            size=100,
            content_type=mime_type
        )

        # 3. Confirm
        c_resp = await client.post(
            "/api/v1/assets/upload/confirm",
            json={
                "workspace_uuid": ws_uuid,
                "asset_uuid": ticket["asset_uuid"],
                "upload_key": ticket["upload_key"]
            },
            headers=headers
        )
        assert c_resp.status_code == 200
        return c_resp.json()["data"]

    # 创建 Image
    img_asset = await create_asset("pic.png", "image/png")
    assert img_asset["type"] == "image" # 验证创建是否正确
    
    # 创建 Document
    doc_asset = await create_asset("note.txt", "text/plain")
    assert doc_asset["type"] == "document" # 验证创建是否正确

    # 3. Test List Filtering
    # Filter by Image
    resp = await client.get(f"/api/v1/assets?workspace_uuid={ws_uuid}&type=image", headers=headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["data"]
    
    # [Assertion] Should be exactly 1 image
    assert len(data) == 1
    assert data[0]["uuid"] == img_asset["uuid"]

    # Filter by Document
    resp = await client.get(f"/api/v1/assets?workspace_uuid={ws_uuid}&type=document", headers=headers)
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["uuid"] == doc_asset["uuid"]
    
    # 4. Test Rename
    new_name = "renamed_pic.png"
    resp = await client.put(
        f"/api/v1/assets/{img_asset['uuid']}",
        json={"name": new_name},
        headers=headers
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["data"]["name"] == new_name

    # 5. Test Move Folder
    f_resp = await client.post(
        f"/api/v1/assets/folders?workspace_uuid={ws_uuid}",
        json={"name": "Docs", "parent_id": None},
        headers=headers
    )
    folder_id = f_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/assets/{doc_asset['uuid']}",
        json={"folder_id": folder_id},
        headers=headers
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["data"]["folder_id"] == folder_id

    # Verify List by Folder
    resp = await client.get(
        f"/api/v1/assets?workspace_uuid={ws_uuid}&folder_id={folder_id}", 
        headers=headers
    )
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["uuid"] == doc_asset["uuid"]

    # 6. Test Delete
    resp = await client.delete(f"/api/v1/assets/{img_asset['uuid']}", headers=headers)
    assert resp.status_code == status.HTTP_200_OK

    # Verify it's gone from list
    resp = await client.get(f"/api/v1/assets?workspace_uuid={ws_uuid}", headers=headers)
    active_uuids = [a["uuid"] for a in resp.json()["data"]]
    assert img_asset["uuid"] not in active_uuids