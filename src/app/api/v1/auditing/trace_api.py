from fastapi import APIRouter

from app.api.dependencies.context import AuthContextDep
from app.core.context import AppContext
from app.schemas.auditing.trace_schemas import (
    TraceFlamegraphRead,
    TraceSummaryRead,
    TraceTreeRead,
)
from app.schemas.common import JsonResponse
from app.services.auditing.trace_service import TraceService


router = APIRouter()


@router.get("/{trace_id}", response_model=JsonResponse[TraceSummaryRead], summary="Get Trace Summary")
async def get_trace_summary(
    trace_id: str,
    context: AppContext = AuthContextDep,
):
    service = TraceService(context)
    return JsonResponse(data=await service.get_trace_summary(trace_id))


@router.get("/{trace_id}/tree", response_model=JsonResponse[TraceTreeRead], summary="Get Trace Call Tree")
async def get_trace_tree(
    trace_id: str,
    context: AppContext = AuthContextDep,
):
    service = TraceService(context)
    return JsonResponse(data=await service.get_trace_tree(trace_id))


@router.get("/{trace_id}/flamegraph", response_model=JsonResponse[TraceFlamegraphRead], summary="Get Trace Flamegraph")
async def get_trace_flamegraph(
    trace_id: str,
    context: AppContext = AuthContextDep,
):
    service = TraceService(context)
    return JsonResponse(data=await service.get_trace_flamegraph(trace_id))
