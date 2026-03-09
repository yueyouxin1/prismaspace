import uuid

import pytest

from app.engine.utils.parameter_schema_utils import schemas2obj
from app.engine.workflow import (
    BaseNodeConfig,
    NodeCategory,
    NodeData,
    NodeExecutionResult,
    NodeResultData,
    NodeTemplate,
    ParameterSchema,
    register_node,
)
from app.engine.workflow.registry import BaseNode
from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionRequest
from app.services.resource.workflow.workflow_service import WorkflowService
from tests.conftest import UserContext


pytestmark = pytest.mark.asyncio


_FLAKY_ATTEMPTS: dict[str, int] = {}


class _FlakyResumeConfig(BaseNodeConfig):
    pass


FLAKY_RESUME_TEMPLATE = NodeTemplate(
    category=NodeCategory.CUSTOM,
    icon="refresh-cw",
    data=NodeData(
        registryId="TestFlakyResumeNode",
        name="Test Flaky Resume Node",
        description="Fail once, then succeed on resume.",
        inputs=[],
        outputs=[ParameterSchema(name="value", type="string")],
        config=_FlakyResumeConfig(),
    ),
    forms=[],
)


@register_node(template=FLAKY_RESUME_TEMPLATE)
class FlakyResumeNode(BaseNode):
    async def execute(self) -> NodeExecutionResult:
        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        item = str(node_input.get("item", "default"))
        seen = _FLAKY_ATTEMPTS.get(item, 0)
        _FLAKY_ATTEMPTS[item] = seen + 1
        if seen == 0:
            raise RuntimeError(f"fail-once:{item}")

        return NodeExecutionResult(
            input=node_input,
            data=NodeResultData(output={"value": f"recovered:{item}"}),
        )


async def _drain_run(run_result) -> tuple[list[dict], dict | None]:
    events: list[dict] = []
    final_payload = None
    async for event in run_result.generator:
        payload = {"event": event.event, "data": event.data}
        events.append(payload)
        if event.event == "finish":
            final_payload = event.data

    if run_result.task and not run_result.task.done():
        await run_result.task
    elif run_result.task:
        await run_result.task

    return events, final_payload


async def test_workflow_run_can_resume_from_latest_checkpoint(
    created_resource_factory,
    app_context_factory,
    registered_user_with_pro: UserContext,
):
    _FLAKY_ATTEMPTS.clear()
    run_key = f"resume-{uuid.uuid4().hex[:8]}"

    resource = await created_resource_factory("workflow")
    actor = registered_user_with_pro.user
    context = await app_context_factory(actor)
    service = WorkflowService(context)

    instance = await service.get_by_uuid(resource.workspace_instance.uuid)
    assert instance is not None

    graph = {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "inputs": [],
                    "outputs": [{"name": "item", "type": "string"}],
                    "config": {},
                },
            },
            {
                "id": "flaky",
                "data": {
                    "registryId": "TestFlakyResumeNode",
                    "name": "Flaky",
                    "inputs": [
                        {
                            "name": "item",
                            "type": "string",
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "start", "path": "item"},
                            },
                        }
                    ],
                    "outputs": [{"name": "value", "type": "string"}],
                    "config": {},
                },
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "inputs": [
                        {
                            "name": "result",
                            "type": "string",
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "flaky", "path": "value"},
                            },
                        }
                    ],
                    "outputs": [],
                    "config": {"returnType": "Object"},
                },
            },
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "flaky", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "flaky", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"},
        ],
    }

    await service.update_instance(instance, {"graph": graph})

    failed_run = await service.async_execute(
        instance.uuid,
        WorkflowExecutionRequest(inputs={"item": run_key}),
        actor,
    )
    failed_events, failed_final = await _drain_run(failed_run)
    assert failed_final is None
    assert any(event["event"] == "error" for event in failed_events)

    failed_detail = await service.get_run(failed_run.run_id)
    assert failed_detail.status == "failed"
    assert failed_detail.can_resume is True
    assert failed_detail.latest_checkpoint is not None
    assert any(node.status == "FAILED" for node in failed_detail.node_executions)

    resumed_run = await service.async_execute(
        instance.uuid,
        WorkflowExecutionRequest(resume_from_run_id=failed_run.run_id),
        actor,
    )
    resumed_events, resumed_final = await _drain_run(resumed_run)
    assert any(event["event"] == "finish" for event in resumed_events)
    assert resumed_final is not None
    assert resumed_final["output"]["result"] == f"recovered:{run_key}"

    resumed_detail = await service.get_run(resumed_run.run_id)
    assert resumed_detail.status == "succeeded"
    assert resumed_detail.parent_run_id == failed_run.run_id
    assert resumed_detail.thread_id == failed_run.thread_id
    assert resumed_detail.can_resume is False
