"""agent_run_event_and_tool_history

Revision ID: b7d4c8e1f2a3
Revises: a3f19d2c4b11
Create Date: 2026-03-09 12:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7d4c8e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a3f19d2c4b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_run_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_execution_id", sa.Integer(), nullable=False),
        sa.Column("agent_instance_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_execution_id"], ["resource_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_instance_id"], ["ai_resource_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["ai_chat_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_execution_id", "sequence_no", name="uq_agent_run_event_sequence"),
    )
    op.create_index(op.f("ix_ai_agent_run_events_resource_execution_id"), "ai_agent_run_events", ["resource_execution_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_events_agent_instance_id"), "ai_agent_run_events", ["agent_instance_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_events_session_id"), "ai_agent_run_events", ["session_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_events_sequence_no"), "ai_agent_run_events", ["sequence_no"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_events_event_type"), "ai_agent_run_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_ai_agent_run_events_created_at"), "ai_agent_run_events", ["created_at"], unique=False)

    op.create_table(
        "ai_agent_tool_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_execution_id", sa.Integer(), nullable=False),
        sa.Column("agent_instance_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("tool_call_id", sa.String(length=100), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=True),
        sa.Column("thought", sa.Text(), nullable=True),
        sa.Column("arguments", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_execution_id"], ["resource_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_instance_id"], ["ai_resource_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["ai_chat_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_execution_id", "tool_call_id", name="uq_agent_tool_execution_call"),
    )
    op.create_index(op.f("ix_ai_agent_tool_executions_resource_execution_id"), "ai_agent_tool_executions", ["resource_execution_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_tool_executions_agent_instance_id"), "ai_agent_tool_executions", ["agent_instance_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_tool_executions_session_id"), "ai_agent_tool_executions", ["session_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_tool_executions_turn_id"), "ai_agent_tool_executions", ["turn_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_tool_executions_tool_call_id"), "ai_agent_tool_executions", ["tool_call_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_tool_executions_tool_name"), "ai_agent_tool_executions", ["tool_name"], unique=False)
    op.create_index(op.f("ix_ai_agent_tool_executions_status"), "ai_agent_tool_executions", ["status"], unique=False)
    op.create_index(op.f("ix_ai_agent_tool_executions_created_at"), "ai_agent_tool_executions", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_agent_tool_executions_created_at"), table_name="ai_agent_tool_executions")
    op.drop_index(op.f("ix_ai_agent_tool_executions_status"), table_name="ai_agent_tool_executions")
    op.drop_index(op.f("ix_ai_agent_tool_executions_tool_name"), table_name="ai_agent_tool_executions")
    op.drop_index(op.f("ix_ai_agent_tool_executions_tool_call_id"), table_name="ai_agent_tool_executions")
    op.drop_index(op.f("ix_ai_agent_tool_executions_turn_id"), table_name="ai_agent_tool_executions")
    op.drop_index(op.f("ix_ai_agent_tool_executions_session_id"), table_name="ai_agent_tool_executions")
    op.drop_index(op.f("ix_ai_agent_tool_executions_agent_instance_id"), table_name="ai_agent_tool_executions")
    op.drop_index(op.f("ix_ai_agent_tool_executions_resource_execution_id"), table_name="ai_agent_tool_executions")
    op.drop_table("ai_agent_tool_executions")

    op.drop_index(op.f("ix_ai_agent_run_events_created_at"), table_name="ai_agent_run_events")
    op.drop_index(op.f("ix_ai_agent_run_events_event_type"), table_name="ai_agent_run_events")
    op.drop_index(op.f("ix_ai_agent_run_events_sequence_no"), table_name="ai_agent_run_events")
    op.drop_index(op.f("ix_ai_agent_run_events_session_id"), table_name="ai_agent_run_events")
    op.drop_index(op.f("ix_ai_agent_run_events_agent_instance_id"), table_name="ai_agent_run_events")
    op.drop_index(op.f("ix_ai_agent_run_events_resource_execution_id"), table_name="ai_agent_run_events")
    op.drop_table("ai_agent_run_events")
