import os
import argparse
import pathlib

# --- [Configuration] ---
PROJECT_ROOT = pathlib.Path(__file__).parent.parent

# --- [Boilerplate Templates] ---

def get_schema_content(service_name: str, class_name: str) -> str:
    # [FIX] Changed inner docstrings to single quotes '''...'''
    return f"""# app/schemas/{service_name}/{service_name}_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime

# TODO: Import any related schemas, like CreatorInfo if needed
# from app.schemas.identity.user_schemas import UserRead

class {class_name}Base(BaseModel):
    '''Base schema for {class_name}.'''
    # TODO: Add fields shared between create and update
    name: str = Field(..., min_length=1, max_length=255, description="{class_name} name")

class {class_name}Create({class_name}Base):
    '''Schema for creating a {class_name}.'''
    pass

class {class_name}Update({class_name}Base):
    '''Schema for updating a {class_name}.'''
    pass

class {class_name}Read({class_name}Base):
    '''Schema for reading a {class_name}.'''
    uuid: str
    created_at: datetime
    updated_at: datetime
    
    # TODO: Add any related objects, like a creator
    # creator: CreatorInfo

    model_config = ConfigDict(from_attributes=True)
"""

def get_dao_content(service_name: str, class_name: str) -> str:
    return f"""# app/dao/{service_name}/{service_name}_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.dao.base_dao import BaseDao
from app.models.{service_name}.{service_name} import {class_name} # TODO: Adjust model import path

class {class_name}Dao(BaseDao[{class_name}]):
    def __init__(self, db_session: AsyncSession):
        super().__init__({class_name}, db_session)

    async def get_by_uuid(self, uuid: str) -> Optional[{class_name}]:
        return await self.get_one(where={{"uuid": uuid}})
"""

def get_service_content(service_name: str, class_name: str) -> str:
    return f"""# app/services/{service_name}/{service_name}_service.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.models import User # TODO: Import the actual model for {class_name}
from app.dao.{service_name}.{service_name}_dao import {class_name}Dao
from app.schemas.{service_name}.{service_name}_schemas import {class_name}Create, {class_name}Update
from app.services.exceptions import NotFoundError
from app.services.permissions.permission_service import PermissionService

class {class_name}Service:
    def __init__(self, db_session: AsyncSession, perm_service: PermissionService):
        self.db = db_session
        self.perm_service = perm_service
        self.dao = {class_name}Dao(db_session)

    async def create_{service_name}(self, data: {class_name}Create, actor: User) -> {class_name}:
        '''Creates a new {service_name} after checking permissions.'''
        # TODO: Add permission check, e.g.:
        # await self.perm_service.ensure_can(["{service_name}:create"], target=related_object)
        
        new_obj = {class_name}(**data.model_dump(), creator_id=actor.id) # Example
        return await self.dao.add(new_obj)

    async def get_{service_name}_by_uuid(self, uuid: str, actor: User) -> {class_name}:
        '''Gets a single {service_name} by UUID, checking for read permissions.'''
        obj = await self.dao.get_by_uuid(uuid)
        if not obj:
            raise NotFoundError("{class_name} not found.")

        # TODO: Add permission check, e.g.:
        # await self.perm_service.ensure_can(["{service_name}:read"], target=obj)
        
        return obj
        
    async def update_{service_name}_by_uuid(self, uuid: str, data: {class_name}Update, actor: User) -> {class_name}:
        '''Updates a {service_name} by UUID, checking for update permissions.'''
        obj = await self.get_{service_name}_by_uuid(uuid, actor) # Re-uses the get method for existence and read permission check

        # TODO: Add specific update permission check if different from read
        # await self.perm_service.ensure_can(["{service_name}:update"], target=obj)

        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(obj, key, value)
            
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def delete_{service_name}_by_uuid(self, uuid: str, actor: User) -> None:
        '''Deletes a {service_name} by UUID, checking for delete permissions.'''
        obj = await self.get_{service_name}_by_uuid(uuid, actor) # Re-uses the get method

        # TODO: Add specific delete permission check
        # await self.perm_service.ensure_can(["{service_name}:delete"], target=obj)
        
        await self.db.delete(obj)
        await self.db.flush()
"""

def get_api_content(service_name: str, class_name: str) -> str:
    return f"""# app/api/v1/{service_name}.py

from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.db.session import get_db
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.{service_name}.{service_name}_schemas import {class_name}Read, {class_name}Create, {class_name}Update
from app.services.{service_name}.{service_name}_service import {class_name}Service
from app.api.dependencies.authentication import get_auth, AuthContext
from app.services.exceptions import PermissionDeniedError, NotFoundError

router = APIRouter()

@router.post("", response_model=JsonResponse[{class_name}Read], status_code=status.HTTP_201_CREATED)
async def create_{service_name}(
    data_in: {class_name}Create,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_auth)
):
    '''Create a new {service_name} endpoint.'''
    try:
        service = {class_name}Service(db, auth.permission_service)
        new_obj = await service.create_{service_name}(data_in, auth.user)
        return JsonResponse(data={class_name}Read.model_validate(new_obj))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    # TODO: Add other specific exception handlers

@router.get("/{{{service_name}_uuid}}", response_model=JsonResponse[{class_name}Read])
async def get_{service_name}(
    {service_name}_uuid: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_auth)
):
    '''Get a single {service_name} by its UUID.'''
    try:
        service = {class_name}Service(db, auth.permission_service)
        obj = await service.get_{service_name}_by_uuid({service_name}_uuid, auth.user)
        return JsonResponse(data={class_name}Read.model_validate(obj))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

# TODO: Add List, Update, and Delete endpoints following the same pattern.
"""

def get_test_content(service_name: str, class_name: str) -> str:
    return f"""# tests/api/v1/test_{service_name}.py

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status
import uuid

from app.models import User # TODO: Import relevant models
from app.core.config import settings

pytestmark = pytest.mark.asyncio

class Test{class_name}Lifecycle:
    
    @pytest.fixture(scope="function")
    async def test_user(self, registered_user_factory) -> User:
        '''Provides a standard user for tests.'''
        return await registered_user_factory(password=settings.DEFAULT_TEST_PASSWORD)

    async def test_create_{service_name}_success(self, client: AsyncClient, auth_headers_factory, test_user: User):
        '''Tests successful creation of a {class_name}.'''
        # Arrange
        headers = await auth_headers_factory(test_user, settings.DEFAULT_TEST_PASSWORD)
        payload = {{
            # TODO: Fill in the required payload for creation
            "name": "Test {class_name}"
        }}

        # Act
        # TODO: Adjust the endpoint path if it's nested
        response = await client.post("/api/v1/{service_name}s", json=payload, headers=headers)

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()["data"]
        assert data["name"] == payload["name"]

    # TODO: Add more tests for get, list, update, delete, and permission failures.
"""

def main():
    parser = argparse.ArgumentParser(description="Generate boilerplate files for a new service in the FastAPI project.")
    parser.add_argument("service_name", type=str, help="The name of the new service (e.g., 'resource', 'billing_account'). Use snake_case.")
    args = parser.parse_args()

    service_name = args.service_name.lower()
    class_name = service_name.replace('_', ' ').title().replace(' ', '')

    print(f"🚀 Generating files for service: '{service_name}' (ClassName: '{class_name}')")

    files_to_create = {
        "schema": {
            "path": PROJECT_ROOT / f"src/app/schemas/{service_name}/{service_name}_schemas.py",
            "content_func": get_schema_content
        },
        "dao": {
            "path": PROJECT_ROOT / f"src/app/dao/{service_name}/{service_name}_dao.py",
            "content_func": get_dao_content
        },
        "service": {
            "path": PROJECT_ROOT / f"src/app/services/{service_name}/{service_name}_service.py",
            "content_func": get_service_content
        },
        "api": {
            "path": PROJECT_ROOT / f"src/app/api/v1/{service_name}.py",
            "content_func": get_api_content
        },
        "test": {
            "path": PROJECT_ROOT / f"tests/api/v1/test_{service_name}.py",
            "content_func": get_test_content
        }
    }

    for file_type, info in files_to_create.items():
        file_path = info["path"]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        if file_path.exists():
            print(f"🟡 Skipping {file_path.relative_to(PROJECT_ROOT)}: File already exists.")
            continue
            
        print(f"✅ Creating {file_path.relative_to(PROJECT_ROOT)}...")
        
        content = info["content_func"](service_name, class_name)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    print("\n🎉 Done! Remember to:")
    print(f"  1. Define the '{class_name}' model in `app/models/`. You may need a new file like `app/models/{service_name}.py`.")
    print(f"  2. Include the new API router in `app/api/router.py`.")
    print(f"  3. Fill in the 'TODO' sections in the generated files.")
    print(f"  4. Add `settings.DEFAULT_TEST_PASSWORD` to your `.env` for the test fixture if it doesn't exist.")

if __name__ == "__main__":
    main()