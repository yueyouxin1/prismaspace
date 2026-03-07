"""repair_c4f8_execution_ledger_drift

Revision ID: f2e6a2d6c0b1
Revises: e1b7c4a2f9d1
Create Date: 2026-03-07 19:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f2e6a2d6c0b1"
down_revision: Union[str, Sequence[str], None] = "e1b7c4a2f9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RESOURCE_EXECUTION_STATUS_VALUES = (
    "pending",
    "running",
    "succeeded",
    "interrupted",
    "cancelled",
    "failed",
)


def _inspector(bind) -> sa.Inspector:
    return sa.inspect(bind)


def _has_table(bind, table_name: str) -> bool:
    return table_name in _inspector(bind).get_table_names()


def _column_map(bind, table_name: str) -> dict[str, dict]:
    return {column["name"]: column for column in _inspector(bind).get_columns(table_name)}


def _has_column(bind, table_name: str, column_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return column_name in _column_map(bind, table_name)


def _has_index(bind, table_name: str, index_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return any(index["name"] == index_name for index in _inspector(bind).get_indexes(table_name))


def _has_foreign_key(bind, table_name: str, constrained_columns: list[str], referred_table: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    for foreign_key in _inspector(bind).get_foreign_keys(table_name):
        if foreign_key.get("referred_table") != referred_table:
            continue
        if foreign_key.get("constrained_columns") == constrained_columns:
            return True
    return False


def _table_row_count(bind, table_name: str) -> int:
    return int(bind.execute(sa.text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one())


def _null_count(bind, table_name: str, column_name: str) -> int:
    return int(
        bind.execute(
            sa.text(f'SELECT COUNT(*) FROM "{table_name}" WHERE "{column_name}" IS NULL')
        ).scalar_one()
    )


def _ensure_index(bind, table_name: str, index_name: str, columns: list[str], unique: bool = False) -> None:
    if not _has_index(bind, table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _ensure_postgres_resource_execution_status(bind) -> None:
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'resourceexecutionstatus'
            ) THEN
                CREATE TYPE resourceexecutionstatus AS ENUM (
                    'pending',
                    'running',
                    'succeeded',
                    'interrupted',
                    'cancelled',
                    'failed'
                );
            END IF;
        END
        $$;
        """
    )

    for enum_value in RESOURCE_EXECUTION_STATUS_VALUES:
        op.execute(f"ALTER TYPE resourceexecutionstatus ADD VALUE IF NOT EXISTS '{enum_value}'")


def _resource_execution_status_type(bind):
    if bind.dialect.name == "postgresql":
        _ensure_postgres_resource_execution_status(bind)
        return postgresql.ENUM(
            *RESOURCE_EXECUTION_STATUS_VALUES,
            name="resourceexecutionstatus",
            create_type=False,
        )
    return sa.Enum(*RESOURCE_EXECUTION_STATUS_VALUES, name="resourceexecutionstatus")


def _ensure_non_nullable(bind, table_name: str, column_name: str, existing_type) -> None:
    column = _column_map(bind, table_name).get(column_name)
    if not column or not column.get("nullable", True):
        return
    if _null_count(bind, table_name, column_name) > 0:
        raise RuntimeError(
            f"Cannot enforce NOT NULL for {table_name}.{column_name}: existing rows still contain NULL values."
        )
    op.alter_column(table_name, column_name, existing_type=existing_type, nullable=False)


def _ensure_resource_executions(bind) -> None:
    status_type = _resource_execution_status_type(bind)

    if not _has_table(bind, "resource_executions"):
        op.create_table(
            "resource_executions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("thread_id", sa.String(length=255), nullable=False),
            sa.Column("parent_run_id", sa.String(length=36), nullable=True),
            sa.Column("resource_instance_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("status", status_type, nullable=False, server_default="pending"),
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
    else:
        row_count = _table_row_count(bind, "resource_executions")
        required_columns = {
            "run_id": sa.Column("run_id", sa.String(length=36), nullable=True),
            "thread_id": sa.Column("thread_id", sa.String(length=255), nullable=True),
            "resource_instance_id": sa.Column("resource_instance_id", sa.Integer(), nullable=True),
            "user_id": sa.Column("user_id", sa.Integer(), nullable=True),
        }
        for column_name, column in required_columns.items():
            if not _has_column(bind, "resource_executions", column_name):
                if row_count > 0:
                    raise RuntimeError(
                        "resource_executions exists but is missing required columns with existing rows. "
                        "Repair this table manually before rerunning migrations."
                    )
                op.add_column("resource_executions", column)

        optional_columns = [
            sa.Column("parent_run_id", sa.String(length=36), nullable=True),
            sa.Column("status", status_type, nullable=False, server_default="pending"),
            sa.Column("trace_id", sa.String(length=36), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        ]
        for column in optional_columns:
            if not _has_column(bind, "resource_executions", column.name):
                op.add_column("resource_executions", column)

    if not _has_foreign_key(bind, "resource_executions", ["resource_instance_id"], "ai_resource_instances"):
        op.create_foreign_key(
            op.f("fk_resource_executions_resource_instance_id_ai_resource_instances"),
            "resource_executions",
            "ai_resource_instances",
            ["resource_instance_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if not _has_foreign_key(bind, "resource_executions", ["user_id"], "users"):
        op.create_foreign_key(
            op.f("fk_resource_executions_user_id_users"),
            "resource_executions",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_run_id"), ["run_id"], unique=True)
    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_thread_id"), ["thread_id"])
    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_parent_run_id"), ["parent_run_id"])
    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_resource_instance_id"), ["resource_instance_id"])
    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_user_id"), ["user_id"])
    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_status"), ["status"])
    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_trace_id"), ["trace_id"])
    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_started_at"), ["started_at"])
    _ensure_index(bind, "resource_executions", op.f("ix_resource_executions_created_at"), ["created_at"])

    _ensure_non_nullable(bind, "resource_executions", "run_id", sa.String(length=36))
    _ensure_non_nullable(bind, "resource_executions", "thread_id", sa.String(length=255))
    _ensure_non_nullable(bind, "resource_executions", "resource_instance_id", sa.Integer())
    _ensure_non_nullable(bind, "resource_executions", "user_id", sa.Integer())
    _ensure_non_nullable(bind, "resource_executions", "status", status_type)
    _ensure_non_nullable(bind, "resource_executions", "created_at", sa.DateTime())
    _ensure_non_nullable(bind, "resource_executions", "updated_at", sa.DateTime())


def _ensure_ai_chat_messages_run_turn_columns(bind) -> None:
    if not _has_column(bind, "ai_chat_messages", "run_id"):
        op.add_column("ai_chat_messages", sa.Column("run_id", sa.String(length=36), nullable=True))
    if not _has_column(bind, "ai_chat_messages", "turn_id"):
        op.add_column("ai_chat_messages", sa.Column("turn_id", sa.String(length=36), nullable=True))

    _ensure_index(bind, "ai_chat_messages", op.f("ix_ai_chat_messages_run_id"), ["run_id"])
    _ensure_index(bind, "ai_chat_messages", op.f("ix_ai_chat_messages_turn_id"), ["turn_id"])

    op.execute(
        """
        UPDATE ai_chat_messages
        SET run_id = COALESCE(trace_id, uuid),
            turn_id = COALESCE(trace_id, uuid)
        WHERE run_id IS NULL OR turn_id IS NULL
        """
    )

    _ensure_non_nullable(bind, "ai_chat_messages", "run_id", sa.String(length=36))
    _ensure_non_nullable(bind, "ai_chat_messages", "turn_id", sa.String(length=36))


def _ensure_ai_agent_context_summaries_run_turn_columns(bind) -> None:
    if not _has_column(bind, "ai_agent_context_summaries", "run_id"):
        op.add_column("ai_agent_context_summaries", sa.Column("run_id", sa.String(length=36), nullable=True))
    if not _has_column(bind, "ai_agent_context_summaries", "turn_id"):
        op.add_column("ai_agent_context_summaries", sa.Column("turn_id", sa.String(length=36), nullable=True))

    summary_columns = _column_map(bind, "ai_agent_context_summaries")
    trace_column = summary_columns.get("trace_id")
    if trace_column and not trace_column.get("nullable", True):
        op.alter_column(
            "ai_agent_context_summaries",
            "trace_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )

    _ensure_index(bind, "ai_agent_context_summaries", op.f("ix_ai_agent_context_summaries_run_id"), ["run_id"])
    _ensure_index(bind, "ai_agent_context_summaries", op.f("ix_ai_agent_context_summaries_turn_id"), ["turn_id"])

    op.execute(
        """
        UPDATE ai_agent_context_summaries
        SET run_id = COALESCE(trace_id, uuid),
            turn_id = COALESCE(trace_id, uuid)
        WHERE run_id IS NULL OR turn_id IS NULL
        """
    )

    _ensure_non_nullable(bind, "ai_agent_context_summaries", "run_id", sa.String(length=36))
    _ensure_non_nullable(bind, "ai_agent_context_summaries", "turn_id", sa.String(length=36))


def _backfill_resource_executions(bind) -> None:
    if bind.dialect.name == "postgresql":
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


def upgrade() -> None:
    bind = op.get_bind()

    _ensure_resource_executions(bind)
    _ensure_ai_chat_messages_run_turn_columns(bind)
    _ensure_ai_agent_context_summaries_run_turn_columns(bind)
    _backfill_resource_executions(bind)


def downgrade() -> None:
    # This migration repairs schema drift on databases already stamped to e1b7.
    # Reverting it would intentionally reintroduce drift, so downgrade is a no-op.
    pass
