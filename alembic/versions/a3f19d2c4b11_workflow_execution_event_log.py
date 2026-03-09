"""workflow_execution_event_log

Revision ID: a3f19d2c4b11
Revises: 7c2d3ef7a1b2
Create Date: 2026-03-09 11:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a3f19d2c4b11"
down_revision: Union[str, Sequence[str], None] = "7c2d3ef7a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_workflow_execution_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_execution_id", sa.Integer(), nullable=False),
        sa.Column("workflow_instance_id", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_execution_id"], ["resource_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_instance_id"], ["ai_resource_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_execution_id",
            "sequence_no",
            name="uq_workflow_execution_event_sequence",
        ),
    )
    op.create_index(op.f("ix_ai_workflow_execution_events_resource_execution_id"), "ai_workflow_execution_events", ["resource_execution_id"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_events_workflow_instance_id"), "ai_workflow_execution_events", ["workflow_instance_id"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_events_sequence_no"), "ai_workflow_execution_events", ["sequence_no"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_events_event_type"), "ai_workflow_execution_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_ai_workflow_execution_events_created_at"), "ai_workflow_execution_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_workflow_execution_events_created_at"), table_name="ai_workflow_execution_events")
    op.drop_index(op.f("ix_ai_workflow_execution_events_event_type"), table_name="ai_workflow_execution_events")
    op.drop_index(op.f("ix_ai_workflow_execution_events_sequence_no"), table_name="ai_workflow_execution_events")
    op.drop_index(op.f("ix_ai_workflow_execution_events_workflow_instance_id"), table_name="ai_workflow_execution_events")
    op.drop_index(op.f("ix_ai_workflow_execution_events_resource_execution_id"), table_name="ai_workflow_execution_events")
    op.drop_table("ai_workflow_execution_events")
