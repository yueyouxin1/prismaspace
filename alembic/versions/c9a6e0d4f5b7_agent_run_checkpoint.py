"""agent_run_checkpoint

Revision ID: c9a6e0d4f5b7
Revises: b7d4c8e1f2a3
Create Date: 2026-03-09 13:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9a6e0d4f5b7"
down_revision: Union[str, Sequence[str], None] = "b7d4c8e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_run_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_execution_id", sa.Integer(), nullable=False),
        sa.Column("agent_instance_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=False),
        sa.Column("checkpoint_kind", sa.String(length=32), nullable=False),
        sa.Column("run_input_payload", sa.JSON(), nullable=False),
        sa.Column("adapted_snapshot", sa.JSON(), nullable=False),
        sa.Column("runtime_snapshot", sa.JSON(), nullable=False),
        sa.Column("pending_client_tool_calls", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_execution_id"], ["resource_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_instance_id"], ["ai_resource_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["ai_chat_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_execution_id"),
    )
    op.create_index(op.f("ix_ai_agent_run_checkpoints_resource_execution_id"), "ai_agent_run_checkpoints", ["resource_execution_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_checkpoints_agent_instance_id"), "ai_agent_run_checkpoints", ["agent_instance_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_checkpoints_session_id"), "ai_agent_run_checkpoints", ["session_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_checkpoints_thread_id"), "ai_agent_run_checkpoints", ["thread_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_checkpoints_turn_id"), "ai_agent_run_checkpoints", ["turn_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_checkpoints_checkpoint_kind"), "ai_agent_run_checkpoints", ["checkpoint_kind"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_checkpoints_created_at"), "ai_agent_run_checkpoints", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_agent_run_checkpoints_created_at"), table_name="ai_agent_run_checkpoints")
    op.drop_index(op.f("ix_ai_agent_run_checkpoints_checkpoint_kind"), table_name="ai_agent_run_checkpoints")
    op.drop_index(op.f("ix_ai_agent_run_checkpoints_turn_id"), table_name="ai_agent_run_checkpoints")
    op.drop_index(op.f("ix_ai_agent_run_checkpoints_thread_id"), table_name="ai_agent_run_checkpoints")
    op.drop_index(op.f("ix_ai_agent_run_checkpoints_session_id"), table_name="ai_agent_run_checkpoints")
    op.drop_index(op.f("ix_ai_agent_run_checkpoints_agent_instance_id"), table_name="ai_agent_run_checkpoints")
    op.drop_index(op.f("ix_ai_agent_run_checkpoints_resource_execution_id"), table_name="ai_agent_run_checkpoints")
    op.drop_table("ai_agent_run_checkpoints")
