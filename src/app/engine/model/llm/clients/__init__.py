# src/app/engine/model/llm/clients/__init__.py
# 这个文件是空的，但它的存在使 'clients' 成为一个包

# 动态导入所有客户端，以确保它们被注册
from . import openai_client
# from . import azure_client # 未来添加
# from . import dashscope_client # 未来添加