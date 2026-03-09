import enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class WorkflowCheckpointReason(str, enum.Enum):
    EXECUTION_START = "execution_start"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_INTERRUPTED = "node_interrupted"
    NODE_SKIPPED = "node_skipped"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_INTERRUPTED = "execution_interrupted"
    EXECUTION_CANCELLED = "execution_cancelled"


workflow_checkpoint_reason_enum = Enum(
    WorkflowCheckpointReason,
    name="workflowcheckpointreason",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class WorkflowNodeExecution(Base):
    """
    工作流节点级执行记录。
    为运行态查询、节点排障与恢复决策提供 durable 观测面。
    """

    __tablename__ = "ai_workflow_node_executions"

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
    node_id = Column(String(64), nullable=False, index=True)
    node_name = Column(String(255), nullable=False)
    node_type = Column(String(64), nullable=False, index=True)
    attempt = Column(Integer, nullable=False, default=1)

    status = Column(String(32), nullable=False, default="PENDING", index=True)
    input = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    activated_port = Column(String(32), nullable=True)
    executed_time = Column(Float, nullable=False, default=0.0)

    started_at = Column(DateTime, nullable=True, index=True)
    finished_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    execution = relationship("ResourceExecution")
    workflow_instance = relationship("Workflow")

    __table_args__ = (
        UniqueConstraint(
            "resource_execution_id",
            "node_id",
            "attempt",
            name="uq_workflow_node_execution_attempt",
        ),
    )


class WorkflowExecutionCheckpoint(Base):
    """
    工作流 durable checkpoint。
    存储恢复所需的 runtime plan 与 runtime state。
    """

    __tablename__ = "ai_workflow_execution_checkpoints"

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
    step_index = Column(Integer, nullable=False, default=0, index=True)
    reason = Column(workflow_checkpoint_reason_enum, nullable=False, index=True)
    node_id = Column(String(64), nullable=True, index=True)

    runtime_plan = Column(JSON, nullable=False, comment="执行期 Runtime IR 快照。")
    payload = Column(JSON, nullable=False, default=dict)
    variables = Column(JSON, nullable=False, default=dict)
    node_states = Column(JSON, nullable=False, default=dict)
    ready_queue = Column(JSON, nullable=False, default=list)

    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)

    execution = relationship("ResourceExecution")
    workflow_instance = relationship("Workflow")
