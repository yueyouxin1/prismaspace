# src/app/worker/__init__.py

# 1. 导入并导出基类和注册表
from .main import (
    WorkerSettings,
    startup,
    shutdown,
    TASK_FUNCTIONS,
    CRON_JOBS
)

# 2. 依次加载所有子域包，这将触发它们的自注册
from . import tasks