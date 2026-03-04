"""ag_ui_message_structured_fields

Revision ID: 8d9f2b0a8a32
Revises: baede282df95
Create Date: 2026-03-04 05:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8d9f2b0a8a32"
down_revision: Union[str, Sequence[str], None] = "baede282df95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_chat_messages", sa.Column("text_content", sa.Text(), nullable=True))
    op.add_column("ai_chat_messages", sa.Column("content_parts", sa.JSON(), nullable=True))
    op.add_column("ai_chat_messages", sa.Column("reasoning_content", sa.Text(), nullable=True))
    op.add_column("ai_chat_messages", sa.Column("activity_type", sa.String(length=64), nullable=True))
    op.add_column("ai_chat_messages", sa.Column("encrypted_value", sa.Text(), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE messagerole ADD VALUE IF NOT EXISTS 'DEVELOPER'")
        op.execute("ALTER TYPE messagerole ADD VALUE IF NOT EXISTS 'ACTIVITY'")
        op.execute("ALTER TYPE messagerole ADD VALUE IF NOT EXISTS 'REASONING'")

        op.execute(
            """
            UPDATE ai_chat_messages
            SET text_content = content
            WHERE text_content IS NULL AND content IS NOT NULL
            """
        )
        op.execute(
            """
            UPDATE ai_chat_messages
            SET reasoning_content = meta->'reasoning'->>'plaintext'
            WHERE reasoning_content IS NULL
              AND meta IS NOT NULL
              AND (meta->'reasoning'->>'plaintext') IS NOT NULL
            """
        )
    else:
        op.execute(
            """
            UPDATE ai_chat_messages
            SET text_content = content
            WHERE text_content IS NULL AND content IS NOT NULL
            """
        )


def downgrade() -> None:
    op.drop_column("ai_chat_messages", "encrypted_value")
    op.drop_column("ai_chat_messages", "activity_type")
    op.drop_column("ai_chat_messages", "reasoning_content")
    op.drop_column("ai_chat_messages", "content_parts")
    op.drop_column("ai_chat_messages", "text_content")

