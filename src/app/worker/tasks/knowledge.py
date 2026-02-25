# src/app/worker/tasks/knowledge.py

import traceback
from app.services.resource.knowledge.knowledge_service import KnowledgeBaseService
from app.worker.context import rebuild_context_for_worker

async def process_document_task(ctx: dict, instance_id: int, document_id: int, file_content: str | list, payload: dict, user_uuid: str):
    """处理单个知识库文档的摄入管道。"""
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid)
                knowledge_service = KnowledgeBaseService(app_context)
                await knowledge_service._process_document_pipeline(instance_id, document_id, file_content, payload)
    except Exception as e:
        print(f"FATAL: Task process_document_task for doc_id: {document_id} failed. Error: {e}")
        traceback.print_exc()
        raise

async def update_chunk_task(ctx: dict, instance_id: int, chunk_id_to_embed: int, user_uuid: str):
    """
    ARQ Worker: Embeds a single chunk and updates its vector_id.
    """
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid)
                knowledge_service = KnowledgeBaseService(app_context)
                
                # Delegate to the refactored pipeline method.
                await knowledge_service._update_chunk_pipeline(
                    instance_id=instance_id,
                    chunk_id_to_embed=chunk_id_to_embed
                )
    except Exception as e:
        print(f"FATAL: Task update_chunk_task for chunk_id: {chunk_id_to_embed} failed. Error: {e}")
        traceback.print_exc()
        raise

async def garbage_collect_document_task(ctx: dict, document_id: int, embedding_module_version_id: str, engine_alias: str, user_uuid: str):
    """
    [NEW - IMPROVED] 执行单个孤儿文档的物理删除，接收所有必要上下文。
    """
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid)
                knowledge_service = KnowledgeBaseService(app_context)
                await knowledge_service._garbage_collect_document(document_id, embedding_module_version_id, engine_alias)
    except Exception as e:
        print(f"FATAL: Task garbage_collect_document_task for doc_id: {document_id} failed. Error: {e}")
        traceback.print_exc()
        raise

async def run_periodic_document_gc_task(ctx: dict):
    """
    [NEW] 定期清道夫任务，扫描并清理所有孤儿文档。
    """
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid=None)
                knowledge_service = KnowledgeBaseService(app_context)
                await knowledge_service.run_periodic_gc()
    except Exception as e:
        print(f"FATAL: Task run_periodic_document_gc_task failed. Error: {e}")
        traceback.print_exc()
        raise