# src/app/api/v1/feature.py

from fastapi import APIRouter, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse
from app.schemas.product.product_schemas import FeatureCreate, FeatureRead
from app.services.product.feature_service import FeatureService
from app.services.exceptions import ServiceException, NotFoundError

router = APIRouter()

@router.post("", response_model=JsonResponse[FeatureRead], status_code=status.HTTP_201_CREATED, summary="[Admin] Create Feature")
async def create_feature(feature_in: FeatureCreate, context: AppContext = AuthContextDep):
    """[Admin] 创建一个新的原子化计费单元 (Feature)。"""
    try:
        service = FeatureService(context)
        new_feature = await service.create_feature(feature_in)
        return JsonResponse(data=new_feature)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("", response_model=JsonResponse[List[FeatureRead]], summary="[Admin] List All Features")
async def list_features(context: AppContext = AuthContextDep):
    """[Admin] 获取平台上定义的所有 Features 列表。"""
    service = FeatureService(context)
    features = await service.list_features()
    return JsonResponse(data=features)

@router.get("/{name}", response_model=JsonResponse[FeatureRead], summary="[Admin] Get Feature by Name")
async def get_feature(name: str, context: AppContext = AuthContextDep):
    """[Admin] 按名称获取单个 Feature 的详细信息。"""
    try:
        service = FeatureService(context)
        feature = await service.get_feature_by_name(name)
        return JsonResponse(data=feature)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))