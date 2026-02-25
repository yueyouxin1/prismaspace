# src/app/worker/tasks/__init__.py

from arq import cron
# 1. 导入并导出这个子域的所有公开任务
from .metering import process_consumption_task
from .asset import process_asset_intelligence_task, physical_delete_asset_task
from .knowledge import (
    process_document_task,
    update_chunk_task,
    garbage_collect_document_task,
    run_periodic_document_gc_task
)
# 2. 导入注册中心
from ..main import TASK_FUNCTIONS, CRON_JOBS

# 3. 将自己注册进去
# 将任务函数添加到注册表
TASK_FUNCTIONS.extend([
    process_consumption_task,
    process_asset_intelligence_task,
    physical_delete_asset_task,
    update_chunk_task,
    process_document_task, 
    garbage_collect_document_task
])

CRON_JOBS = [
    # 配置周期性任务，每天凌晨3点运行
    cron(run_periodic_document_gc_task, hour=3, minute=0)
]