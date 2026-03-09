"""workflow_runtime_durable_execution

Revision ID: 7c2d3ef7a1b2
Revises: f2e6a2d6c0b1
Create Date: 2026-03-09 08:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7c2d3ef7a1b2"
down_revision: Union[str, Sequence[str], None] = "f2e6a2d6c0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


workflow_checkpoint_reason = sa.Enum(
    "execution_start",
    "node_completed",
    "node_failed",
    "node_interrupted",
    "node_skipped",
    "execution_succeeded",
    "execution_failed",
    "execution_interrupted",
    "execution_cancelled",
    name="workflowcheckpointreason",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "ai_workflow_node_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_execution_id", sa.Integer(), nullable=False),
        sa.Column("workflow_instance_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("node_name", sa.String(length=255), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("activated_port", sa.String(length=32), nullable=True),
        sa.Column("executed_time", sa.Float(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_execution_id"], ["resource_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_instance_id"], ["ai_resource_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_execution_id",
            "node_id",
            "attempt",
            name="uq_workflow_node_execution_attempt",
        ),
    )
    op.create_index(op.f("ix_ai_workflow_node_executions_resource_execution_id"), "ai_workflow_node_executions", ["resource_execution_id"], unique=False)
    op.create_index(op.f("ix_ai_workflow_node_executions_workflow_instance_id"), "ai_workflow_node_executions", ["workflow_instance_id"], unique=False)
    op.create_index(op.f("ix_ai_workflow_node_executions_node_id"), "ai_workflow_node_executions", ["node_id"], unique=False)
    op.create_index(op.f("ix_ai_workflow_node_executions_node_type"), "ai_workflow_node_executions", ["node_type"], unique=False)
    op.create_index(op.f("ix_ai_workflow_node_executions_status"), "ai_workflow_node_executions", ["status"], unique=False)
    op.create_index(op.f("ix_ai_workflow_node_executions_started_at"), "ai_workflow_node_executions", ["started_at"], unique=False)
    op.create_index(op.f("ix_ai_workflow_node_executions_finished_at"), "ai_workflow_node_executions", ["finished_at"], unique=False)
    op.create_index(op.f("ix_ai_workflow_node_executions_created_at"), "ai_workflow_node_executions", ["created_at"], unique=False)

    op.create_table(
        "ai_workflow_execution_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_execution_id", sa.Integer(), nullable=False),
        sa.Column("workflow_instance_id", sa.Integer(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", workflow_checkpoint_reason, nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_plan", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("node_states", sa.JSON(), nullable=False),
        sa.Column("ready_queue", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_execution_id"], ["resource_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_instance_id"], ["ai_resource_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_workflow_execution_checkpoints_resource_execution_id"), "ai_workflow_execution_checkpoints", ["resource_execution_id"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_checkpoints_workflow_instance_id"), "ai_workflow_execution_checkpoints", ["workflow_instance_id"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_checkpoints_step_index"), "ai_workflow_execution_checkpoints", ["step_index"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_checkpoints_reason"), "ai_workflow_execution_checkpoints", ["reason"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_checkpoints_node_id"), "ai_workflow_execution_checkpoints", ["node_id"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_checkpoints_created_at"), "ai_workflow_execution_checkpoints", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_workflow_execution_checkpoints_created_at"), table_name="ai_workflow_execution_checkpoints")
    op.drop_index(op.f("ix_ai_workflow_execution_checkpoints_node_id"), table_name="ai_workflow_execution_checkpoints")
    op.drop_index(op.f("ix_ai_workflow_execution_checkpoints_reason"), table_name="ai_workflow_execution_checkpoints")
    op.drop_index(op.f("ix_ai_workflow_execution_checkpoints_step_index"), table_name="ai_workflow_execution_checkpoints")
    op.drop_index(op.f("ix_ai_workflow_execution_checkpoints_workflow_instance_id"), table_name="ai_workflow_execution_checkpoints")
    op.drop_index(op.f("ix_ai_workflow_execution_checkpoints_resource_execution_id"), table_name="ai_workflow_execution_checkpoints")
    op.drop_table("ai_workflow_execution_checkpoints")

    op.drop_index(op.f("ix_ai_workflow_node_executions_created_at"), table_name="ai_workflow_node_executions")
    op.drop_index(op.f("ix_ai_workflow_node_executions_finished_at"), table_name="ai_workflow_node_executions")
    op.drop_index(op.f("ix_ai_workflow_node_executions_started_at"), table_name="ai_workflow_node_executions")
    op.drop_index(op.f("ix_ai_workflow_node_executions_status"), table_name="ai_workflow_node_executions")
    op.drop_index(op.f("ix_ai_workflow_node_executions_node_type"), table_name="ai_workflow_node_executions")
    op.drop_index(op.f("ix_ai_workflow_node_executions_node_id"), table_name="ai_workflow_node_executions")
    op.drop_index(op.f("ix_ai_workflow_node_executions_workflow_instance_id"), table_name="ai_workflow_node_executions")
    op.drop_index(op.f("ix_ai_workflow_node_executions_resource_execution_id"), table_name="ai_workflow_node_executions")
    op.drop_table("ai_workflow_node_executions")

    workflow_checkpoint_reason.drop(op.get_bind(), checkfirst=True)
