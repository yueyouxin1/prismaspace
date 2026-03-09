import pytest
from sqlalchemy import text

from app.models import ResourceExecutionStatus
from app.services.resource.execution.execution_ledger_service import ExecutionLedgerService
from tests.conftest import UserContext


pytestmark = pytest.mark.asyncio


async def test_execution_ledger_status_persists_lowercase_enum_values(
    created_resource_factory,
    app_context_factory,
    registered_user_with_pro: UserContext,
    db_session,
):
    resource = await created_resource_factory("agent")
    instance = resource.workspace_instance
    actor = registered_user_with_pro.user
    context = await app_context_factory(actor)
    service = ExecutionLedgerService(context)

    success_execution = await service.create_execution(instance=instance, actor=actor, thread_id="session-1")
    await service.mark_running(success_execution, trace_id="trace-success")
    await service.mark_finished(success_execution, status=ResourceExecutionStatus.SUCCEEDED)

    failed_execution = await service.create_execution(instance=instance, actor=actor, thread_id="session-2")
    await service.mark_finished(
        failed_execution,
        status=ResourceExecutionStatus.FAILED,
        error_code="TEST_FAILURE",
        error_message="expected failure",
    )

    status_rows = await db_session.execute(
        text(
            """
            SELECT run_id, CAST(status AS TEXT) AS status
            FROM resource_executions
            WHERE run_id IN (:success_run_id, :failed_run_id)
            ORDER BY run_id
            """
        ),
        {
            "success_run_id": success_execution.run_id,
            "failed_run_id": failed_execution.run_id,
        },
    )
    persisted = {row.run_id: row.status for row in status_rows}

    assert persisted[success_execution.run_id] == "succeeded"
    assert persisted[failed_execution.run_id] == "failed"

    await db_session.refresh(success_execution)
    await db_session.refresh(failed_execution)

    assert success_execution.status == ResourceExecutionStatus.SUCCEEDED
    assert success_execution.trace_id == "trace-success"
    assert success_execution.thread_id == "session-1"
    assert failed_execution.status == ResourceExecutionStatus.FAILED
    assert failed_execution.error_code == "TEST_FAILURE"
    assert failed_execution.error_message == "expected failure"


async def test_execution_ledger_parent_resolution_requires_same_thread(
    created_resource_factory,
    app_context_factory,
    registered_user_with_pro: UserContext,
):
    resource = await created_resource_factory("agent")
    instance = resource.workspace_instance
    actor = registered_user_with_pro.user
    context = await app_context_factory(actor)
    service = ExecutionLedgerService(context)

    parent = await service.create_execution(
        instance=instance,
        actor=actor,
        thread_id="thread-1",
    )

    resolved = await service.resolve_parent_execution(
        parent_run_id=parent.run_id,
        instance=instance,
        actor=actor,
        thread_id="thread-1",
    )
    assert resolved is not None
    assert resolved.run_id == parent.run_id

    assert await service.resolve_parent_execution(
        parent_run_id=parent.run_id,
        instance=instance,
        actor=actor,
        thread_id="thread-2",
    ) is None

    retry = await service.create_execution(
        instance=instance,
        actor=actor,
        thread_id="thread-1",
        parent_run_id=parent.run_id,
    )
    resumed = await service.create_execution(
        instance=instance,
        actor=actor,
        thread_id="thread-1",
        parent_run_id=retry.run_id,
    )

    root_run_id = await service.resolve_lineage_root_run_id(
        execution=resumed,
        instance=instance,
        actor=actor,
        thread_id="thread-1",
    )
    assert root_run_id == parent.run_id


async def test_execution_ledger_active_lookup_includes_pending_and_running(
    created_resource_factory,
    app_context_factory,
    registered_user_with_pro: UserContext,
):
    resource = await created_resource_factory("agent")
    instance = resource.workspace_instance
    actor = registered_user_with_pro.user
    context = await app_context_factory(actor)
    service = ExecutionLedgerService(context)

    pending_execution = await service.create_execution(instance=instance, actor=actor, thread_id="thread-1")
    running_execution = await service.create_execution(instance=instance, actor=actor, thread_id="thread-2")
    interrupted_execution = await service.create_execution(instance=instance, actor=actor, thread_id="thread-3")

    await service.mark_running(running_execution)
    await service.mark_finished(interrupted_execution, status=ResourceExecutionStatus.INTERRUPTED)

    active_pending = await service.get_latest_active_execution(
        instance=instance,
        actor=actor,
        thread_id="thread-1",
    )
    active_running = await service.get_latest_active_execution(
        instance=instance,
        actor=actor,
        thread_id="thread-2",
    )
    inactive_interrupted = await service.get_latest_active_execution(
        instance=instance,
        actor=actor,
        thread_id="thread-3",
    )

    assert active_pending is not None
    assert active_pending.run_id == pending_execution.run_id
    assert active_running is not None
    assert active_running.run_id == running_execution.run_id
    assert inactive_interrupted is None
