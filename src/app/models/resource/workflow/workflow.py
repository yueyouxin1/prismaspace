import enum
from sqlalchemy import Column, Integer, String, Text, JSON, Enum, ForeignKey, DateTime, func, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.models.resource.base import ResourceInstance
from app.utils.id_generator import generate_uuid

class Workflow(ResourceInstance):
    """
    Workflow 资源实例。存储工作流的图结构定义 (DSL) 及 IO 契约。
    """
    __tablename__ = 'ai_workflows'

    version_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), primary_key=True)
    
    # 存储完整的 WorkflowGraph JSON (DSL)
    graph = Column(JSON, nullable=False, comment="工作流的DSL图结构")
    
    # [增强] IO 契约，用于调用者感知
    # 对应 Start 节点的 outputs 定义
    inputs_schema = Column(JSON, nullable=False, default=[], comment="工作流输入参数定义 (List[ParameterSchema])")
    
    # 对应 End 节点的 outputs 定义 (或者 End 节点的输入源结构)
    outputs_schema = Column(JSON, nullable=False, default=[], comment="工作流输出结构定义 (List[ParameterSchema])")
    
    # 标记该工作流是否设计为流式输出 (通常由 End 节点的配置决定)
    is_stream = Column(Boolean, nullable=False, default=False, comment="是否支持流式输出")
    
    __mapper_args__ = { 'polymorphic_identity': 'workflow' }

class WorkflowNodeDef(Base):
    """
    [元数据] 节点模版定义表。
    完全对齐 NodeTemplate Schema。
    """
    __tablename__ = 'ai_workflow_node_defs'
    
    id = Column(Integer, primary_key=True)
    registry_id = Column(String(50), unique=True, nullable=False, index=True, comment="节点全局唯一标识")
    
    # UI 元数据
    category = Column(String(50), nullable=False, index=True)
    icon = Column(String(255), nullable=True)
    display_order = Column(Integer, default=0)
    
    data = Column(JSON, nullable=False, comment="节点的预设数据结构 (NodeData)")
    
    # [核心] 对应 NodeTemplate.forms
    forms = Column(JSON, nullable=False, comment="节点的 UI 表单定义")
    
    is_active = Column(Boolean, default=True)