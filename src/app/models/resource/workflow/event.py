import enum

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class WorkflowExecutionEventType(str, enum.Enum):
    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_INTERRUPTED = "run.interrupted"
    NODE_STARTED = "node.started"
    NODE_COMPLETED = "node.completed"
    NODE_FAILED = "node.failed"
    NODE_SKIPPED = "node.skipped"
    STREAM_STARTED = "stream.started"
    STREAM_DELTA = "stream.delta"
    STREAM_FINISHED = "stream.finished"
    CHECKPOINT_CREATED = "checkpoint.created"
    SYSTEM_ERROR = "system.error"


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
