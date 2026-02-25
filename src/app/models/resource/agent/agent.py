# src/app/models/resource/agent/agent.py

from sqlalchemy import Column, Integer, String, Text, JSON, Float, ForeignKey
from sqlalchemy.orm import relationship
from app.models.resource.base import ResourceInstance

class Agent(ResourceInstance):
    """
    Agent 资源实例。
    代表一个配置好的智能体，包含了模型参数、提示词以及绑定的工具引用。
    """
    __tablename__ = 'ai_agents'

    version_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), primary_key=True)
    
    # --- 模型配置 ---
    # 这里的 provider/model 对应 ServiceModule
    agent_config = Column(JSON, nullable=False, default={}, comment="AgentConfig (temperature, top_p, etc.)")
    
    # --- 提示词工程 ---
    system_prompt = Column(Text, nullable=True, comment="System level instruction")
    
    # --- 关联的推理模型 ---
    # 我们直接关联一个 ServiceModuleVersion (LLM)
    llm_module_version_id = Column(Integer, ForeignKey('service_module_versions.id'), nullable=False)
    llm_module_version = relationship("ServiceModuleVersion", lazy="joined")

    __mapper_args__ = { 'polymorphic_identity': 'agent' }