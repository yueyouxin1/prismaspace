# src/app/worker/tasks/asset.py

import logging
from app.worker.context import rebuild_context_for_worker
from app.core.storage.factory import get_storage_provider

logger = logging.getLogger(__name__)

async def process_asset_intelligence_task(ctx: dict, asset_uuid: str, user_uuid: str):
    """
    ARQ Task: 
    1. Downloads the asset stream.
    2. Identifies type (Image/Audio/Doc).
    3. Calls specific AI models (VLM/ASR/LLM) via ServiceModule.
    4. Generates embeddings.
    5. Updates Asset.meta and VectorDB.
    """
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid)
                provider = get_storage_provider()
                # 1. 锁定并检查状态
                # 2. 下载文件
                # 3. 识别类型与选择处理器
                # 4. 向量化 (System Index)
                # 5. 更新 Intelligence
                
    except Exception as e:
        logger.error(f"Failed to process asset {asset_uuid}: {e}", exc_info=True)

async def physical_delete_asset_task(ctx: dict, asset_uuid: str):
    """
    ARQ Task: Performs the physical deletion from Object Storage.
    """
    logger.info(f"[Worker] Physically deleting {asset_uuid}")
    try:
        # Assuming provider configuration is globally available or reconstructed
        provider = get_storage_provider()
        success = await provider.delete_object(asset_uuid)
        if not success:
            logger.warning(f"Storage provider returned false for deletion of {asset_uuid}")
    except Exception as e:
        logger.error(f"Physical deletion failed for {asset_uuid}: {e}", exc_info=True)