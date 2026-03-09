import enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class AgentToolExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    AWAITING_CLIENT = "awaiting_client"
    CANCELLED = "cancelled"
    ERROR = "error"


class AgentRunEvent(Base):
    """
    Agent durable event log。
    承载 AG-UI 事件的持久化时间线，用于 run replay、审计与调试。
    """

    __tablename__ = "ai_agent_run_events"

    id = Column(Integer, primary_key=True)
    resource_execution_id = Column(
        Integer,
        ForeignKey("resource_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_instance_id = Column(
        Integer,
        ForeignKey("ai_resource_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(
        Integer,
        ForeignKey("ai_chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sequence_no = Column(Integer, nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)

    execution = relationship("ResourceExecution")
    agent_instance = relationship("Agent")
    session = relationship("AgentSession")

    __table_args__ = (
        UniqueConstraint(
            "resource_execution_id",
            "sequence_no",
            name="uq_agent_run_event_sequence",
        ),
    )


class AgentToolExecution(Base):
    """
    Agent tool / step 执行历史。
    """

    __tablename__ = "ai_agent_tool_executions"

    id = Column(Integer, primary_key=True)
    resource_execution_id = Column(
        Integer,
        ForeignKey("resource_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_instance_id = Column(
        Integer,
        ForeignKey("ai_resource_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(
        Integer,
        ForeignKey("ai_chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    turn_id = Column(String(36), nullable=True, index=True)
    tool_call_id = Column(String(100), nullable=False, index=True)
    tool_name = Column(String(255), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    step_index = Column(Integer, nullable=True)
    thought = Column(Text, nullable=True)
    arguments = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    execution = relationship("ResourceExecution")
    agent_instance = relationship("Agent")
    session = relationship("AgentSession")

    __table_args__ = (
        UniqueConstraint(
            "resource_execution_id",
            "tool_call_id",
            name="uq_agent_tool_execution_call",
        ),
    )


class AgentRunCheckpoint(Base):
    """
    Agent 运行时 checkpoint。
    用于恢复 interrupt run 的上下文快照，而不是只依赖会话历史重放。
    """

    __tablename__ = "ai_agent_run_checkpoints"

    id = Column(Integer, primary_key=True)
    resource_execution_id = Column(
        Integer,
        ForeignKey("resource_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,
    )
    agent_instance_id = Column(
        Integer,
        ForeignKey("ai_resource_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(
        Integer,
        ForeignKey("ai_chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    thread_id = Column(String(255), nullable=False, index=True)
    turn_id = Column(String(36), nullable=False, index=True)
    checkpoint_kind = Column(String(32), nullable=False, index=True)
    run_input_payload = Column(JSON, nullable=False, default=dict)
    adapted_snapshot = Column(JSON, nullable=False, default=dict)
    runtime_snapshot = Column(JSON, nullable=False, default=dict)
    pending_client_tool_calls = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    execution = relationship("ResourceExecution")
    agent_instance = relationship("Agent")
    session = relationship("AgentSession")
