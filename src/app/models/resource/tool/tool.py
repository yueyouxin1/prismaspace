# src/app/models/resource/tool/tool.py

from sqlalchemy import (
    Column, Integer, String, JSON, ForeignKey,JSON
)
from app.models.resource.base import ResourceInstance

class Tool(ResourceInstance):
    """Tool实现 - ResourceInstance的一个具体子类，存储Tool特有的实现数据。"""
    __tablename__ = 'ai_tools'
    # 必要
    version_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), primary_key=True)
    # 必要
    url = Column(String(2048), nullable=True, comment="工具的API端点URL")
    # 必要
    method = Column(String(10), nullable=False, comment="HTTP请求方法 (GET, POST, etc.)")
    # 必要
    inputs_schema = Column(JSON, nullable=False, comment="输入参数的JSON Schema")
    # 必要
    outputs_schema = Column(JSON, nullable=False, comment="输出结果的JSON Schema")
    # 增强/AI
    llm_function_schema = Column(JSON, nullable=True, comment="提供给大语言模型的Function Calling Schema")

    __mapper_args__ = { 'polymorphic_identity': 'tool' }