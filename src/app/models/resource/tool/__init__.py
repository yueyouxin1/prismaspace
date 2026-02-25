# app/models/resource/tool/__init__.py

# 1. 导入并导出这个子域的所有公开模型
from .tool import Tool

# 2. 导入注册中心
from ..base import ALL_INSTANCE_TYPES

# 3. 将自己注册进去
ALL_INSTANCE_TYPES.append(Tool)