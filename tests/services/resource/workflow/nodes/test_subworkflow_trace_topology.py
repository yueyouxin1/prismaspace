from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.trace_manager import TraceManager
from app.engine.workflow.definitions import NodeData, WorkflowNode
from app.engine.workflow.definitions import NodeResultData
from app.services.resource.workflow.nodes.node import AppWorkflowNode
from app.services.resource.workflow.nodes.template import WORKFLOW_TEMPLATE


pytestmark = pytest.mark.asyncio


class _FakeAsyncSession:
    def __init__(self):
        self.added = []

    def add_all(self, items):
        self.added.extend(items)

    async def flush(self):
        return None

    async def commit(self):
        return None


async def test_subworkflow_node_creates_child_workflow_run_span(monkeypatch):
    import sys

    db = _FakeAsyncSession()
    actor = SimpleNamespace(id=7)
    child_instance = SimpleNamespace(id=220, uuid="workflow-child", graph={})
    child_execution = SimpleNamespace(run_id="run-child", thread_id="thread-child")

    class _FakeWorkflowService:
        def __init__(self, _context):
            self.runtime_compiler = SimpleNamespace(compile=lambda graph: "plan")
            self.execution_ledger_service = SimpleNamespace(
                create_execution=AsyncMock(return_value=child_execution),
                mark_running=AsyncMock(),
                mark_finished=AsyncMock(),
            )
            self.engine_service = SimpleNamespace(
                run=AsyncMock(return_value=NodeResultData(output={"ok": True}))
            )

        async def get_by_uuid(self, uuid):
            return child_instance

    monkeypatch.setitem(
        sys.modules,
        "app.services.resource.workflow.workflow_service",
        SimpleNamespace(
            WorkflowService=_FakeWorkflowService,
            ExternalContext=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.resource.workflow.interceptors",
        SimpleNamespace(WorkflowTraceInterceptor=lambda **kwargs: SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.resource.workflow.runtime_persistence",
        SimpleNamespace(WorkflowDurableRuntimeObserver=lambda **kwargs: SimpleNamespace()),
    )

    node = WorkflowNode(
        id="subworkflow-node",
        data=NodeData(
            registryId=WORKFLOW_TEMPLATE.data.registryId,
            name=WORKFLOW_TEMPLATE.data.name,
            description=WORKFLOW_TEMPLATE.data.description,
            config=WORKFLOW_TEMPLATE.data.config.model_copy(update={"resource_instance_uuid": "workflow-child"}),
            inputs=[],
            outputs=[],
        ),
    )
    runtime_context = SimpleNamespace(
        variables={},
        external_context=SimpleNamespace(
            app_context=SimpleNamespace(db=db, actor=actor),
            runtime_workspace=SimpleNamespace(id=9),
            trace_id="trace-subworkflow-1",
            run_id="run-parent",
            thread_id="thread-parent",
        ),
    )
    executor = AppWorkflowNode(runtime_context, node, False)

    async with TraceManager(
        db=db,
        operation_name="workflow.run",
        user_id=actor.id,
        force_trace_id="trace-subworkflow-1",
        target_instance_id=110,
    ):
        async with TraceManager(
            db=db,
            operation_name="workflow.node.workflownode.subworkflow-node",
        ):
            result = await executor.execute()

    assert result.data.output["ok"] is True
    workflow_run_spans = [item for item in db.added if item.operation_name == "workflow.run"]
    assert len(workflow_run_spans) == 2
    child_span = workflow_run_spans[1]
    parent_node_span = next(item for item in db.added if item.operation_name == "workflow.node.workflownode.subworkflow-node")
    assert child_span.parent_span_uuid == parent_node_span.span_uuid
    assert child_span.source_instance_id == 110
    assert child_span.target_instance_id == 220
