from fastapi import APIRouter, Depends, Path
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.resource.agent.agent_memory_schemas import AgentMemoryVarCreate, AgentMemoryVarUpdate, AgentMemoryVarRead
from app.services.resource.agent.memory.agent_memory_var_service import AgentMemoryVarService
from app.services.resource.agent.agent_service import AgentService # 用于鉴权

router = APIRouter()

@router.get("", response_model=JsonResponse[List[AgentMemoryVarRead]])
async def list_agent_memories(
    agent_uuid: str, 
    context: AppContext = AuthContextDep
):
    agent_service = AgentService(context)
    agent = await agent_service.get_by_uuid(agent_uuid)
    # 鉴权：Read
    await agent_service._check_execute_perm(agent) # 或者更细粒度的 read 权限
    
    service = AgentMemoryVarService(context)
    memories = await service.list_memories(agent.version_id)
    return JsonResponse(data=memories)

@router.post("", response_model=JsonResponse[AgentMemoryVarRead])
async def create_agent_memory(
    agent_uuid: str, 
    data: AgentMemoryVarCreate,
    context: AppContext = AuthContextDep
):
    agent_service = AgentService(context)
    agent = await agent_service.get_by_uuid(agent_uuid)
    # 鉴权：Update (必须是开发者)
    await context.perm_evaluator.ensure_can(["resource:update"], target=agent.resource.workspace)
    
    service = AgentMemoryVarService(context)
    new_memory = await service.create_memory(agent.version_id, data)
    return JsonResponse(data=new_memory)

@router.put("/{memory_id}", response_model=JsonResponse[AgentMemoryVarRead])
async def update_agent_memory(
    agent_uuid: str,
    memory_id: int,
    data: AgentMemoryVarUpdate,
    context: AppContext = AuthContextDep
):
    agent_service = AgentService(context)
    agent = await agent_service.get_by_uuid(agent_uuid)
    # 鉴权
    await context.perm_evaluator.ensure_can(["resource:update"], target=agent.resource.workspace)
    
    service = AgentMemoryVarService(context)
    updated = await service.update_memory(agent.version_id, memory_id, data)
    return JsonResponse(data=updated)

@router.delete("/{memory_id}", response_model=MsgResponse)
async def delete_agent_memory(
    agent_uuid: str,
    memory_id: int,
    context: AppContext = AuthContextDep
):
    agent_service = AgentService(context)
    agent = await agent_service.get_by_uuid(agent_uuid)
    # 鉴权
    await context.perm_evaluator.ensure_can(["resource:update"], target=agent.resource.workspace)
    
    service = AgentMemoryVarService(context)
    await service.delete_memory(agent.version_id, memory_id)
    return MsgResponse(msg="Memory deleted")
