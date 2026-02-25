# src/app/api/v1/entitlement.py

from fastapi import APIRouter, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse
from app.services.billing.entitlement_service import EntitlementService
from app.schemas.billing.entitlement_schemas import EntitlementBalanceRead
from app.services.exceptions import NotFoundError

router = APIRouter()

@router.get("/me", response_model=JsonResponse[List[EntitlementBalanceRead]], summary="Get My Entitlements")
async def get_my_entitlements(context: AppContext = AuthContextDep):
    service = EntitlementService(context)
    balances = await service.list_balances_for_owner(context.actor)
    return JsonResponse(data=balances)

@router.get("/teams/{team_uuid}", response_model=JsonResponse[List[EntitlementBalanceRead]], summary="Get Team Entitlements")
async def get_team_entitlements(team_uuid: str, context: AppContext = AuthContextDep):
    """
    Retrieves a list of all active entitlements for a specific team.
    The current user must have permission to view the team's billing information.
    """
    # The API layer now simply dispatches the request to the service layer.
    # All complex logic is encapsulated within the service.
    try:
        service = EntitlementService(context)
        balances = await service.list_balances_for_team(team_uuid, context.actor)
        return JsonResponse(data=balances)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # PermissionDeniedError is handled by the global exception handler, resulting in a 403.
