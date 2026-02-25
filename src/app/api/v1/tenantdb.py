# app/api/v1/resource.py

from fastapi import APIRouter, Depends, Body, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Any, Dict, List
from pydantic import ValidationError
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.services.resource.resource_service import ResourceService
from app.services.resource.tenantdb_service import TenantDbService
from app.schemas.resource.tenantdb.tenantdb_schemas import (
    TenantTableRead, TenantTableCreate, TenantTableUpdate
)
from app.services.exceptions import PermissionDeniedError, NotFoundError, ServiceException

router = APIRouter() # /tenantdb/{uuid}

@router.post(
    "/{instance_uuid}/tables",
    response_model=JsonResponse[TenantTableRead],
    summary="Create a new Table in a TenantDB instance",
    tags=["Resources - TenantDB"] # 使用新标签分组
)
async def create_tenant_table(
    instance_uuid: str,
    table_data: TenantTableCreate,
    context: AppContext = AuthContextDep
):
    try:
        # 分派到专家服务
        tenantdb_service = TenantDbService(context)
        new_table = await tenantdb_service.create_table(instance_uuid, table_data)
        return JsonResponse(data=new_table)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (NotFoundError, ServiceException, ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put(
    "/{instance_uuid}/tables/{table_uuid}",
    response_model=JsonResponse[TenantTableRead],
    summary="Update a Table in a TenantDB instance",
    tags=["Resources - TenantDB"]
)
async def update_tenant_table(
    instance_uuid: str,
    table_uuid: str,
    update_data: TenantTableUpdate,
    context: AppContext = AuthContextDep
):
    try:
        # 分派到专家服务
        tenantdb_service = TenantDbService(context)
        new_table = await tenantdb_service.update_table(instance_uuid, table_uuid, update_data)
        return JsonResponse(data=new_table)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (NotFoundError, ServiceException, ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete(
    "/{instance_uuid}/tables/{table_uuid}",
    response_model=MsgResponse,
    summary="Delete a Table from a TenantDB instance",
    tags=["Resources - TenantDB"]
)
async def delete_tenant_table(
    instance_uuid: str,
    table_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        # 分派到专家服务
        tenantdb_service = TenantDbService(context)
        await tenantdb_service.delete_table(instance_uuid, table_uuid)
        return MsgResponse(msg="Table deleted successfully.")
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (NotFoundError, ServiceException, ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get(
    "/{instance_uuid}/tables",
    response_model=JsonResponse[List[TenantTableRead]],
    summary="List all Tables in a TenantDB instance",
    tags=["Resources - TenantDB"]
)
async def list_tenant_tables(
    instance_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        tenantdb_service = TenantDbService(context)
        tables = await tenantdb_service.get_tables(instance_uuid)
        return JsonResponse(data=tables)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (NotFoundError, ServiceException, ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get(
    "/{instance_uuid}/tables/{table_uuid}",
    response_model=JsonResponse[TenantTableRead],
    summary="Get a single Table's schema in a TenantDB instance",
    tags=["Resources - TenantDB"]
)
async def get_tenant_table(
    instance_uuid: str,
    table_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        tenantdb_service = TenantDbService(context)
        table = await tenantdb_service.get_table_by_uuid(instance_uuid, table_uuid)
        return JsonResponse(data=table)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (NotFoundError, ServiceException, ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))
