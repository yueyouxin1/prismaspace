"""agent_runtime_hotpath_indexes

Revision ID: d1e4f7a9b2c3
Revises: c9a6e0d4f5b7
Create Date: 2026-03-10 03:20:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d1e4f7a9b2c3"
down_revision: Union[str, Sequence[str], None] = "c9a6e0d4f5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_resource_executions_active_lookup",
        "resource_executions",
        ["resource_instance_id", "user_id", "thread_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_chat_messages_session_deleted_turn",
        "ai_chat_messages",
        ["session_id", "is_deleted", "turn_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_chat_messages_session_deleted_turn", table_name="ai_chat_messages")
    op.drop_index("ix_resource_executions_active_lookup", table_name="resource_executions")
