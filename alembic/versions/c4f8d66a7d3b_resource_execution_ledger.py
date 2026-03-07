"""resource_execution_ledger

Revision ID: c4f8d66a7d3b
Revises: 8d9f2b0a8a32
Create Date: 2026-03-06 23:55:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4f8d66a7d3b"
down_revision: Union[str, Sequence[str], None] = "8d9f2b0a8a32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


resource_execution_status = sa.Enum(
    "pending",
    "running",
    "succeeded",
    "interrupted",
    "cancelled",
    "failed",
    name="resourceexecutionstatus",
)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "resource_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("parent_run_id", sa.String(length=36), nullable=True),
        sa.Column("resource_instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", resource_execution_status, nullable=False, server_default="pending"),
        sa.Column("trace_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_instance_id"], ["ai_resource_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_resource_executions_run_id"), "resource_executions", ["run_id"], unique=True)
    op.create_index(op.f("ix_resource_executions_thread_id"), "resource_executions", ["thread_id"], unique=False)
    op.create_index(op.f("ix_resource_executions_parent_run_id"), "resource_executions", ["parent_run_id"], unique=False)
    op.create_index(op.f("ix_resource_executions_resource_instance_id"), "resource_executions", ["resource_instance_id"], unique=False)
    op.create_index(op.f("ix_resource_executions_user_id"), "resource_executions", ["user_id"], unique=False)
    op.create_index(op.f("ix_resource_executions_status"), "resource_executions", ["status"], unique=False)
    op.create_index(op.f("ix_resource_executions_trace_id"), "resource_executions", ["trace_id"], unique=False)
    op.create_index(op.f("ix_resource_executions_started_at"), "resource_executions", ["started_at"], unique=False)
    op.create_index(op.f("ix_resource_executions_created_at"), "resource_executions", ["created_at"], unique=False)

    op.add_column("ai_chat_messages", sa.Column("run_id", sa.String(length=36), nullable=True))
    op.add_column("ai_chat_messages", sa.Column("turn_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_ai_chat_messages_run_id"), "ai_chat_messages", ["run_id"], unique=False)
    op.create_index(op.f("ix_ai_chat_messages_turn_id"), "ai_chat_messages", ["turn_id"], unique=False)

    op.add_column("ai_agent_context_summaries", sa.Column("run_id", sa.String(length=36), nullable=True))
    op.add_column("ai_agent_context_summaries", sa.Column("turn_id", sa.String(length=36), nullable=True))
    op.alter_column("ai_agent_context_summaries", "trace_id", existing_type=sa.String(length=36), nullable=True)
    op.create_index(op.f("ix_ai_agent_context_summaries_run_id"), "ai_agent_context_summaries", ["run_id"], unique=False)
    op.create_index(op.f("ix_ai_agent_context_summaries_turn_id"), "ai_agent_context_summaries", ["turn_id"], unique=False)

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE ai_chat_messages
            SET run_id = COALESCE(trace_id, uuid),
                turn_id = COALESCE(trace_id, uuid)
            WHERE run_id IS NULL OR turn_id IS NULL
            """
        )
        op.execute(
            """
            UPDATE ai_agent_context_summaries
            SET run_id = COALESCE(trace_id, uuid),
                turn_id = COALESCE(trace_id, uuid)
            WHERE run_id IS NULL OR turn_id IS NULL
            """
        )
        op.execute(
            """
            INSERT INTO resource_executions (
                run_id,
                thread_id,
                parent_run_id,
                resource_instance_id,
                user_id,
                status,
                trace_id,
                started_at,
                finished_at,
                created_at,
                updated_at
            )
            SELECT
                cm.run_id,
                cs.uuid,
                NULL,
                cs.agent_instance_id,
                cs.user_id,
                'succeeded',
                MAX(cm.trace_id),
                MIN(cm.created_at),
                MAX(cm.created_at),
                MIN(cm.created_at),
                MAX(cm.created_at)
            FROM ai_chat_messages cm
            JOIN ai_chat_sessions cs ON cs.id = cm.session_id
            WHERE cm.run_id IS NOT NULL
            GROUP BY cm.run_id, cs.uuid, cs.agent_instance_id, cs.user_id
            ON CONFLICT (run_id) DO NOTHING
            """
        )
    else:
        op.execute(
            """
            UPDATE ai_chat_messages
            SET run_id = COALESCE(trace_id, uuid),
                turn_id = COALESCE(trace_id, uuid)
            WHERE run_id IS NULL OR turn_id IS NULL
            """
        )
        op.execute(
            """
            UPDATE ai_agent_context_summaries
            SET run_id = COALESCE(trace_id, uuid),
                turn_id = COALESCE(trace_id, uuid)
            WHERE run_id IS NULL OR turn_id IS NULL
            """
        )
        op.execute(
            """
            INSERT OR IGNORE INTO resource_executions (
                run_id,
                thread_id,
                parent_run_id,
                resource_instance_id,
                user_id,
                status,
                trace_id,
                started_at,
                finished_at,
                created_at,
                updated_at
            )
            SELECT
                cm.run_id,
                cs.uuid,
                NULL,
                cs.agent_instance_id,
                cs.user_id,
                'succeeded',
                MAX(cm.trace_id),
                MIN(cm.created_at),
                MAX(cm.created_at),
                MIN(cm.created_at),
                MAX(cm.created_at)
            FROM ai_chat_messages cm
            JOIN ai_chat_sessions cs ON cs.id = cm.session_id
            WHERE cm.run_id IS NOT NULL
            GROUP BY cm.run_id, cs.uuid, cs.agent_instance_id, cs.user_id
            """
        )

    op.alter_column("ai_chat_messages", "run_id", existing_type=sa.String(length=36), nullable=False)
    op.alter_column("ai_chat_messages", "turn_id", existing_type=sa.String(length=36), nullable=False)
    op.alter_column("ai_agent_context_summaries", "run_id", existing_type=sa.String(length=36), nullable=False)
    op.alter_column("ai_agent_context_summaries", "turn_id", existing_type=sa.String(length=36), nullable=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_agent_context_summaries_turn_id"), table_name="ai_agent_context_summaries")
    op.drop_index(op.f("ix_ai_agent_context_summaries_run_id"), table_name="ai_agent_context_summaries")
    op.alter_column("ai_agent_context_summaries", "trace_id", existing_type=sa.String(length=36), nullable=False)
    op.drop_column("ai_agent_context_summaries", "turn_id")
    op.drop_column("ai_agent_context_summaries", "run_id")

    op.drop_index(op.f("ix_ai_chat_messages_turn_id"), table_name="ai_chat_messages")
    op.drop_index(op.f("ix_ai_chat_messages_run_id"), table_name="ai_chat_messages")
    op.drop_column("ai_chat_messages", "turn_id")
    op.drop_column("ai_chat_messages", "run_id")

    op.drop_index(op.f("ix_resource_executions_created_at"), table_name="resource_executions")
    op.drop_index(op.f("ix_resource_executions_started_at"), table_name="resource_executions")
    op.drop_index(op.f("ix_resource_executions_trace_id"), table_name="resource_executions")
    op.drop_index(op.f("ix_resource_executions_status"), table_name="resource_executions")
    op.drop_index(op.f("ix_resource_executions_user_id"), table_name="resource_executions")
    op.drop_index(op.f("ix_resource_executions_resource_instance_id"), table_name="resource_executions")
    op.drop_index(op.f("ix_resource_executions_parent_run_id"), table_name="resource_executions")
    op.drop_index(op.f("ix_resource_executions_thread_id"), table_name="resource_executions")
    op.drop_index(op.f("ix_resource_executions_run_id"), table_name="resource_executions")
    op.drop_table("resource_executions")

    if op.get_bind().dialect.name == "postgresql":
        resource_execution_status.drop(op.get_bind(), checkfirst=True)
