import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.utils.id_generator import generate_uuid


class ResourceExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"


resource_execution_status_enum = Enum(
    ResourceExecutionStatus,
    name="resourceexecutionstatus",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class ResourceExecution(Base):
    """
    轻量级平台执行台账。
    仅记录资源级通用执行锚点，不承载协议/会话/上下文等运行态细节。
    """

    __tablename__ = "resource_executions"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    thread_id = Column(String(255), nullable=False, index=True, comment="逻辑执行线程 ID")
    parent_run_id = Column(String(36), nullable=True, index=True, comment="上游执行 ID（resume/retry/regenerate 谱系）")
    resource_instance_id = Column(Integer, ForeignKey("ai_resource_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(resource_execution_status_enum, nullable=False, default=ResourceExecutionStatus.PENDING, index=True)
    trace_id = Column(String(36), nullable=True, index=True, comment="可选的观测 Trace ID")

    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)

    started_at = Column(DateTime, nullable=True, index=True)
    finished_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    resource_instance = relationship("ResourceInstance")
    user = relationship("User")
