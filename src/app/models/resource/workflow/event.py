import enum

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class WorkflowExecutionEventType(str, enum.Enum):
    START = "start"
    NODE_START = "node_start"
    NODE_FINISH = "node_finish"
    NODE_ERROR = "node_error"
    NODE_SKIPPED = "node_skipped"
    STREAM_START = "stream_start"
    STREAM_CHUNK = "stream_chunk"
    STREAM_END = "stream_end"
    INTERRUPT = "interrupt"
    ERROR = "error"
    FINISH = "finish"
    SYSTEM_ERROR = "system_error"


class WorkflowExecutionEvent(Base):
    """
    Workflow durable event log.
    为 run replay、审计与调试时间线提供可检索事件流。
    """

    __tablename__ = "ai_workflow_execution_events"

    id = Column(Integer, primary_key=True)
    resource_execution_id = Column(
        Integer,
        ForeignKey("resource_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_instance_id = Column(
        Integer,
        ForeignKey("ai_resource_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_no = Column(Integer, nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)

    execution = relationship("ResourceExecution")
    workflow_instance = relationship("Workflow")

    __table_args__ = (
        UniqueConstraint(
            "resource_execution_id",
            "sequence_no",
            name="uq_workflow_execution_event_sequence",
        ),
    )
