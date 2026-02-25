# app/models/resource/__init__.py

# 1. 导入并导出基类和注册表
from .base import (
    ResourceType,
    ResourceCategory,
    Resource,
    ResourceRef,
    ProjectResourceRef,
    ResourceInstance,
    ResourceStatus,
    VersionStatus,
    AuthType,
    ALL_INSTANCE_TYPES
)

# 2. 依次加载所有子域包，这将触发它们的自注册
from .uiapp import *
from .agent import *
from .tool import *
from .tenantdb import *
from .knowledge import *
from .workflow import *
