"""resource_tool_schema_and_chat_turn_count

Revision ID: e1b7c4a2f9d1
Revises: c4f8d66a7d3b
Create Date: 2026-03-07 15:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1b7c4a2f9d1"
down_revision: Union[str, Sequence[str], None] = "c4f8d66a7d3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_resource_instances", sa.Column("tool_schema", sa.JSON(), nullable=True))
    op.add_column(
        "ai_chat_sessions",
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
    )

    chat_sessions = sa.table(
        "ai_chat_sessions",
        sa.column("id", sa.Integer()),
        sa.column("turn_count", sa.Integer()),
    )
    chat_messages = sa.table(
        "ai_chat_messages",
        sa.column("session_id", sa.Integer()),
        sa.column("turn_id", sa.String()),
        sa.column("is_deleted", sa.Boolean()),
    )

    active_turn_count = (
        sa.select(sa.func.count(sa.distinct(chat_messages.c.turn_id)))
        .where(
            chat_messages.c.session_id == chat_sessions.c.id,
            chat_messages.c.is_deleted.is_(False),
            chat_messages.c.turn_id.is_not(None),
        )
        .scalar_subquery()
    )
    op.execute(sa.update(chat_sessions).values(turn_count=active_turn_count))
    op.alter_column("ai_chat_sessions", "turn_count", server_default=None)


def downgrade() -> None:
    op.drop_column("ai_chat_sessions", "turn_count")
    op.drop_column("ai_resource_instances", "tool_schema")
