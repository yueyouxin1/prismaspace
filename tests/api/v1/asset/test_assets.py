import pytest
from fastapi import status
from httpx import AsyncClient

from app.core.storage.base import ObjectMetadata
from app.models.asset import AssetType, IntelligenceStatus


@pytest.mark.asyncio
async def test_asset_upload_flow(client: AsyncClient, registered_user_with_pro, auth_headers_factory, mock_storage_provider):
    user_ctx = registered_user_with_pro
    headers = await auth_headers_factory(user_ctx)
    ws_uuid = user_ctx.personal_workspace.uuid

    ticket_payload = {
        "filename": "vacation.jpg",
        "size_bytes": 102400,
        "mime_type": "image/jpeg",
    }
    ticket_resp = await client.post(
        f"/api/v1/assets/upload/ticket?workspace_uuid={ws_uuid}",
        json=ticket_payload,
        headers=headers,
    )
    assert ticket_resp.status_code == status.HTTP_200_OK
    ticket_data = ticket_resp.json()["data"]
    assert ticket_data["asset_uuid"]
    assert ticket_data["upload_url"]

    confirm_payload = {
        "workspace_uuid": ws_uuid,
        "asset_uuid": ticket_data["asset_uuid"],
        "upload_key": ticket_data["upload_key"],
        "name": "My Vacation Photo",
    }
    confirm_resp = await client.post("/api/v1/assets/upload/confirm", json=confirm_payload, headers=headers)
    assert confirm_resp.status_code == status.HTTP_200_OK
    asset_data = confirm_resp.json()["data"]

    assert asset_data["name"] == "My Vacation Photo"
    assert asset_data["type"] == AssetType.IMAGE
    assert asset_data["ai_status"] == IntelligenceStatus.PENDING

    # Idempotent confirm.
    confirm_resp_again = await client.post("/api/v1/assets/upload/confirm", json=confirm_payload, headers=headers)
    assert confirm_resp_again.status_code == status.HTTP_200_OK
    assert confirm_resp_again.json()["data"]["uuid"] == asset_data["uuid"]


@pytest.mark.asyncio
async def test_asset_management_v2_api(client: AsyncClient, registered_user_with_pro, auth_headers_factory, mock_storage_provider):
    user_ctx = registered_user_with_pro
    headers = await auth_headers_factory(user_ctx)
    ws_uuid = user_ctx.personal_workspace.uuid

    async def create_folder(name: str, parent_uuid: str | None = None) -> dict:
        resp = await client.post(
            f"/api/v1/assets/folders?workspace_uuid={ws_uuid}",
            json={"name": name, "parent_uuid": parent_uuid},
            headers=headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        return resp.json()["data"]

    async def create_asset(filename: str, mime_type: str, folder_uuid: str | None = None) -> dict:
        ticket_resp = await client.post(
            f"/api/v1/assets/upload/ticket?workspace_uuid={ws_uuid}",
            json={
                "filename": filename,
                "size_bytes": 100,
                "mime_type": mime_type,
                "folder_uuid": folder_uuid,
            },
            headers=headers,
        )
        assert ticket_resp.status_code == status.HTTP_200_OK
        ticket = ticket_resp.json()["data"]

        mock_storage_provider.get_object_metadata.return_value = ObjectMetadata(
            hash_str=f"hash_{filename}",
            size=100,
            content_type=mime_type,
        )
        confirm_resp = await client.post(
            "/api/v1/assets/upload/confirm",
            json={
                "workspace_uuid": ws_uuid,
                "asset_uuid": ticket["asset_uuid"],
                "upload_key": ticket["upload_key"],
                "folder_uuid": folder_uuid,
                "name": filename,
            },
            headers=headers,
        )
        assert confirm_resp.status_code == status.HTTP_200_OK
        return confirm_resp.json()["data"]

    root_folder = await create_folder("Docs")
    child_folder = await create_folder("Reports", parent_uuid=root_folder["uuid"])

    img_asset = await create_asset("pic.png", "image/png")
    doc_asset = await create_asset("report.txt", "text/plain", folder_uuid=child_folder["uuid"])

    list_image_resp = await client.get(
        f"/api/v1/assets?workspace_uuid={ws_uuid}&type=image",
        headers=headers,
    )
    assert list_image_resp.status_code == status.HTTP_200_OK
    image_data = list_image_resp.json()["data"]
    assert image_data["total"] == 1
    assert image_data["items"][0]["uuid"] == img_asset["uuid"]

    list_docs_resp = await client.get(
        f"/api/v1/assets?workspace_uuid={ws_uuid}&folder_uuid={root_folder['uuid']}&include_subfolders=true",
        headers=headers,
    )
    assert list_docs_resp.status_code == status.HTTP_200_OK
    docs_data = list_docs_resp.json()["data"]
    uuids = {item["uuid"] for item in docs_data["items"]}
    assert doc_asset["uuid"] in uuids

    keyword_resp = await client.get(
        f"/api/v1/assets?workspace_uuid={ws_uuid}&keyword=report",
        headers=headers,
    )
    assert keyword_resp.status_code == status.HTTP_200_OK
    assert keyword_resp.json()["data"]["items"][0]["uuid"] == doc_asset["uuid"]

    detail_resp = await client.get(f"/api/v1/assets/{img_asset['uuid']}", headers=headers)
    assert detail_resp.status_code == status.HTTP_200_OK
    assert detail_resp.json()["data"]["uuid"] == img_asset["uuid"]

    patch_resp = await client.patch(
        f"/api/v1/assets/{img_asset['uuid']}",
        json={"name": "renamed-pic.png", "folder_uuid": root_folder["uuid"]},
        headers=headers,
    )
    assert patch_resp.status_code == status.HTTP_200_OK
    assert patch_resp.json()["data"]["name"] == "renamed-pic.png"
    assert patch_resp.json()["data"]["folder_uuid"] == root_folder["uuid"]

    delete_resp = await client.delete(f"/api/v1/assets/{img_asset['uuid']}", headers=headers)
    assert delete_resp.status_code == status.HTTP_200_OK

    list_after_delete = await client.get(f"/api/v1/assets?workspace_uuid={ws_uuid}", headers=headers)
    assert list_after_delete.status_code == status.HTTP_200_OK
    active_uuids = [item["uuid"] for item in list_after_delete.json()["data"]["items"]]
    assert img_asset["uuid"] not in active_uuids
