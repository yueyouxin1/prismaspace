import pytest
from fastapi import status
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_folder_lifecycle_and_tree(client: AsyncClient, registered_user_with_pro, auth_headers_factory):
    user_ctx = registered_user_with_pro
    headers = await auth_headers_factory(user_ctx)
    ws_uuid = user_ctx.personal_workspace.uuid

    root_resp = await client.post(
        f"/api/v1/assets/folders?workspace_uuid={ws_uuid}",
        json={"name": "Designs"},
        headers=headers,
    )
    assert root_resp.status_code == status.HTTP_200_OK
    root = root_resp.json()["data"]

    child_resp = await client.post(
        f"/api/v1/assets/folders?workspace_uuid={ws_uuid}",
        json={"name": "Logos", "parent_uuid": root["uuid"]},
        headers=headers,
    )
    assert child_resp.status_code == status.HTTP_200_OK
    child = child_resp.json()["data"]
    assert child["parent_uuid"] == root["uuid"]

    roots_resp = await client.get(f"/api/v1/assets/folders?workspace_uuid={ws_uuid}", headers=headers)
    assert roots_resp.status_code == status.HTTP_200_OK
    roots = roots_resp.json()["data"]
    assert any(folder["uuid"] == root["uuid"] for folder in roots)
    assert not any(folder["uuid"] == child["uuid"] for folder in roots)

    children_resp = await client.get(
        f"/api/v1/assets/folders?workspace_uuid={ws_uuid}&parent_uuid={root['uuid']}",
        headers=headers,
    )
    assert children_resp.status_code == status.HTTP_200_OK
    children = children_resp.json()["data"]
    assert len(children) == 1
    assert children[0]["uuid"] == child["uuid"]

    tree_resp = await client.get(f"/api/v1/assets/folders/tree?workspace_uuid={ws_uuid}", headers=headers)
    assert tree_resp.status_code == status.HTTP_200_OK
    tree = tree_resp.json()["data"]
    assert len(tree) == 1
    assert tree[0]["uuid"] == root["uuid"]
    assert len(tree[0]["children"]) == 1
    assert tree[0]["children"][0]["uuid"] == child["uuid"]

    patch_resp = await client.patch(
        f"/api/v1/assets/folders/{child['uuid']}",
        json={"name": "Brand Logos"},
        headers=headers,
    )
    assert patch_resp.status_code == status.HTTP_200_OK
    assert patch_resp.json()["data"]["name"] == "Brand Logos"

    delete_root_resp = await client.delete(f"/api/v1/assets/folders/{root['uuid']}", headers=headers)
    assert delete_root_resp.status_code == status.HTTP_400_BAD_REQUEST

    delete_child_resp = await client.delete(f"/api/v1/assets/folders/{child['uuid']}", headers=headers)
    assert delete_child_resp.status_code == status.HTTP_200_OK

    delete_root_resp_2 = await client.delete(f"/api/v1/assets/folders/{root['uuid']}", headers=headers)
    assert delete_root_resp_2.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_folder_move_cycle_protection(client: AsyncClient, registered_user_with_pro, auth_headers_factory):
    user_ctx = registered_user_with_pro
    headers = await auth_headers_factory(user_ctx)
    ws_uuid = user_ctx.personal_workspace.uuid

    root = (
        await client.post(
            f"/api/v1/assets/folders?workspace_uuid={ws_uuid}",
            json={"name": "A"},
            headers=headers,
        )
    ).json()["data"]
    child = (
        await client.post(
            f"/api/v1/assets/folders?workspace_uuid={ws_uuid}",
            json={"name": "B", "parent_uuid": root["uuid"]},
            headers=headers,
        )
    ).json()["data"]
    grandchild = (
        await client.post(
            f"/api/v1/assets/folders?workspace_uuid={ws_uuid}",
            json={"name": "C", "parent_uuid": child["uuid"]},
            headers=headers,
        )
    ).json()["data"]

    cycle_resp = await client.patch(
        f"/api/v1/assets/folders/{root['uuid']}",
        json={"parent_uuid": grandchild["uuid"]},
        headers=headers,
    )
    assert cycle_resp.status_code == status.HTTP_400_BAD_REQUEST
