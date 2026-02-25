# src/app/engine/model/embedding/clients/__init__.py
# 动态导入所有客户端，以确保它们被注册
from . import openai_client
# from . import zhipu_client  # 未来添加