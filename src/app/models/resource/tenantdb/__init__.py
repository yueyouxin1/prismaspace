# src/app/models/resource/tenantdb/__init__.py

# 1. 导入并导出这个子域的所有公开模型
from .tenantdb import TenantDB
from .tenant_table import TenantTable
from .tenant_column import TenantColumn, TenantDataType

# 2. 导入注册中心
from ..base import ALL_INSTANCE_TYPES

# 3. 将自己注册进去
ALL_INSTANCE_TYPES.append(TenantDB)