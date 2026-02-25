# src/app/api/v1/knowledge.py

import asyncio
import json
from sse_starlette.sse import EventSourceResponse
from fastapi import APIRouter, Query, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.models import DocumentProcessingStatus
from app.services.resource.knowledge.knowledge_service import KnowledgeBaseService
from app.schemas.resource.knowledge.knowledge_schemas import DocumentRead, DocumentRead, DocumentCreate, DocumentUpdate, BatchChunkUpdate, PaginatedDocumentsResponse
from app.services.exceptions import PermissionDeniedError, NotFoundError, ServiceException

router = APIRouter()

@router.post(
    "/{instance_uuid}/documents",
    response_model=JsonResponse[DocumentRead],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Add a document to a KnowledgeBase workspace version",
    tags=["Resources - KnowledgeBase"]
)
async def add_document_to_knowledge_base(
    instance_uuid: str,
    doc_create: DocumentCreate,
    context: AppContext = AuthContextDep
):
    """
    Adds a document (by URL) to a workspace version of a KnowledgeBase instance.
    This triggers a background processing task.
    """
    service = KnowledgeBaseService(context)
    document = await service.add_document_to_version(
        instance_uuid=instance_uuid,
        source_uri=str(doc_create.source_uri), # 转换为字符串
        file_name=doc_create.file_name
    )
    return JsonResponse(data=document, status_code=status.HTTP_202_ACCEPTED)

@router.put(
    "/{instance_uuid}/documents/{document_uuid}",
    response_model=JsonResponse[DocumentRead],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update a document in a KnowledgeBase workspace version",
    tags=["Resources - KnowledgeBase"]
)
async def update_document_in_knowledge_base(
    instance_uuid: str,
    document_uuid: str,
    doc_update: DocumentUpdate,
    context: AppContext = AuthContextDep
):
    """
    Updates a document's metadata (like file_name) or completely replaces it
    with a new file from a new source_uri.
    """
    service = KnowledgeBaseService(context)
    document = await service.update_document_in_version(
        instance_uuid=instance_uuid,
        document_uuid_to_update=document_uuid,
        new_source_uri=str(doc_update.source_uri) if doc_update.source_uri else None,
        new_file_name=doc_update.file_name
    )
    return JsonResponse(data=document, status_code=status.HTTP_202_ACCEPTED)

@router.put(
    "/{instance_uuid}/chunks",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update the content of one or more document chunks in a batch",
    tags=["Resources - KnowledgeBase"]
)
async def update_chunks_in_knowledge_base(
    instance_uuid: str,
    batch_update: BatchChunkUpdate,
    context: AppContext = AuthContextDep
):
    """
    Atomically updates the text content of one or more chunks within the same document.
    This operation uses a Copy-on-Write strategy to ensure version safety and
    triggers background tasks for re-embedding the modified chunks.
    """
    service = KnowledgeBaseService(context)
    # 调用新的服务方法
    await service.update_chunk_content(
        instance_uuid=instance_uuid,
        batch_update_data=batch_update
    )
    return JsonResponse(
        data={"message": "Batch chunk update task has been successfully enqueued."},
        status_code=status.HTTP_202_ACCEPTED
    )

@router.get(
    "/{instance_uuid}/documents",
    response_model=JsonResponse[PaginatedDocumentsResponse],
    summary="List documents in a specific KnowledgeBase version"
)
async def list_documents_in_knowledge_base(
    instance_uuid: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    context: AppContext = AuthContextDep
):
    service = KnowledgeBaseService(context)
    paginated_result = await service.get_documents_in_version(instance_uuid, page, limit)
    return JsonResponse(data=paginated_result)

@router.delete(
    "/{instance_uuid}/documents/{document_uuid}",
    response_model=MsgResponse,
    summary="Remove a document from a KnowledgeBase workspace version"
)
async def remove_document_from_knowledge_base(
    instance_uuid: str,
    document_uuid: str,
    context: AppContext = AuthContextDep
):
    """
    Removes the association of a document from a workspace version.
    This does not immediately delete the physical data if it's used by other versions.
    """
    service = KnowledgeBaseService(context)
    await service.remove_document_from_version(instance_uuid, document_uuid)
    return MsgResponse(msg="Document association removed from this version.")

@router.get(
    "/tasks/{task_id}/progress",
    summary="Subscribe to document processing progress via SSE",
    tags=["Resources - KnowledgeBase"]
)
async def get_document_task_progress(task_id: str, context: AppContext = AuthContextDep):
    """
    Establishes a Server-Sent Events (SSE) connection to stream the progress
    of a document processing task. The task_id is the UUID of the document.
    """
    service = KnowledgeBaseService(context)
    redis_client = service.redis.client

    async def event_generator():
        # 1. 立即发送当前状态
        initial_progress = await service.get_task_progress(task_id)
        if initial_progress:
            yield {"event": "progress", "data": initial_progress.model_dump_json()}
        else:
            # 如果任务不存在，发送错误并关闭
            yield {"event": "error", "data": json.dumps({"message": "Task not found."})}
            return

        # 2. 订阅Redis Pub/Sub频道以获取实时更新
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(service._TASK_PROGRESS_CHANNEL)
        
        try:
            while True:
                # 等待来自频道的消息
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30)
                
                if message and message["type"] == "message":
                    updated_task_id = message["data"]
                    
                    # 检查是否是我们关心的任务
                    if updated_task_id == task_id:
                        progress = await service.get_task_progress(task_id)
                        if progress:
                            yield {"event": "progress", "data": progress.model_dump_json()}
                            if progress.status in [DocumentProcessingStatus.COMPLETED, DocumentProcessingStatus.FAILED]:
                                # 任务结束，关闭连接
                                break
                
                # 发送一个心跳以保持连接活跃
                yield {"event": "ping", "data": ""}

        except asyncio.CancelledError:
            # 客户端断开连接
            print(f"Client disconnected from SSE for task {task_id}")
        finally:
            await pubsub.unsubscribe(service._TASK_PROGRESS_CHANNEL)
            await pubsub.close()

    return EventSourceResponse(event_generator())