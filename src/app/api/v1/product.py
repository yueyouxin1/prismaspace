# src/app/api/v1/product.py (Updated/New)

from fastapi import APIRouter, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep, PublicContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.product.product_schemas import ProductCreateFull, ProductReadFull
from app.services.product.product_service import ProductService
from app.services.exceptions import ServiceException, NotFoundError

router = APIRouter()

@router.post("", response_model=JsonResponse[ProductReadFull], status_code=status.HTTP_201_CREATED, summary="[Admin] Create a Full Product")
async def create_full_product(product_in: ProductCreateFull, context: AppContext = AuthContextDep):
    """[Admin] Creates a complete product with its prices and entitlements in one transaction."""
    try:
        service = ProductService(context)
        new_product = await service.create_full_product(product_in)
        return JsonResponse(data=new_product)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/public", response_model=JsonResponse[List[ProductReadFull]], summary="[Public] List Purchasable Products")
async def list_public_products(context: AppContext = PublicContextDep):
    """[Public] Gets all publicly available products for the pricing page."""
    service = ProductService(context)
    products = await service.list_public_products()
    return JsonResponse(data=products)

@router.get("", response_model=JsonResponse[List[ProductReadFull]], summary="[Admin] List All Products")
async def list_all_products(context: AppContext = AuthContextDep):
    """[Admin] Gets all products for the management dashboard."""
    service = ProductService(context)
    products = await service.list_all_products_for_admin()
    return JsonResponse(data=products)

@router.post("/{product_name}/publish", response_model=JsonResponse[ProductReadFull], summary="[Admin] Publish Product")
async def publish_product(product_name: str, context: AppContext = AuthContextDep):
    """[Admin] Makes a product visible on the public pricing page."""
    try:
        service = ProductService(context)
        product = await service.publish_product(product_name)
        return JsonResponse(data=product)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{product_name}/unpublish", response_model=JsonResponse[ProductReadFull], summary="[Admin] Unpublish Product")
async def unpublish_product(product_name: str, context: AppContext = AuthContextDep):
    """[Admin] Hides a product from the public pricing page."""
    try:
        service = ProductService(context)
        product = await service.unpublish_product(product_name)
        return JsonResponse(data=product)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))