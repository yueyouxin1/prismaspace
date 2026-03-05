from pydantic import TypeAdapter

from app.schemas.resource.execution_schemas import (
    AnyExecutionRequest,
    AnyExecutionResponse,
    GenericExecutionRequest,
    GenericExecutionResponse,
    normalize_execution_request,
)
from app.schemas.resource.agent.agent_schemas import AgentExecutionRequest, AgentExecutionResponse
from app.schemas.resource.knowledge.knowledge_schemas import KnowledgeBaseExecutionRequest
from app.schemas.resource.tenantdb.tenantdb_schemas import TenantDbExecutionRequest
from app.schemas.resource.tool_schemas import ToolExecutionRequest
from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionResponse


def test_any_execution_request_parses_tenantdb_payload():
    adapter = TypeAdapter(AnyExecutionRequest)
    parsed = adapter.validate_python(
        {
            "inputs": {
                "action": "query",
                "table_name": "users",
            }
        }
    )

    assert isinstance(parsed, TenantDbExecutionRequest)
    assert parsed.inputs.action == "query"
    assert parsed.inputs.table_name == "users"


def test_any_execution_request_parses_knowledge_payload():
    adapter = TypeAdapter(AnyExecutionRequest)
    parsed = adapter.validate_python(
        {
            "inputs": {
                "query": "what is prismaspace",
            }
        }
    )

    assert isinstance(parsed, KnowledgeBaseExecutionRequest)
    assert parsed.inputs.query == "what is prismaspace"


def test_generic_execution_request_and_response_for_internal_callers():
    request = GenericExecutionRequest(
        inputs={"city": "tokyo"},
        meta={"timeout_ms": 1000},
    )
    response = GenericExecutionResponse(data={"ok": True})

    assert request.inputs == {"city": "tokyo"}
    assert request.meta == {"timeout_ms": 1000}
    assert response.success is True
    assert response.data == {"ok": True}


def test_any_execution_request_parses_agent_wrapper_payload():
    adapter = TypeAdapter(AnyExecutionRequest)
    parsed = adapter.validate_python(
        {
            "inputs": {
                "threadId": "thread-1",
                "runId": "run-1",
                "state": {},
                "messages": [{"id": "u1", "role": "user", "content": "hello"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            }
        }
    )

    assert isinstance(parsed, AgentExecutionRequest)
    assert parsed.inputs.thread_id == "thread-1"


def test_any_execution_response_parses_workflow_response():
    adapter = TypeAdapter(AnyExecutionResponse)
    parsed = adapter.validate_python(
        {
            "success": True,
            "data": {
                "output": {"ok": True},
                "content": None,
                "error_msg": None,
                "trace_id": "trace-1",
            },
        }
    )

    assert isinstance(parsed, WorkflowExecutionResponse)
    assert parsed.data.trace_id == "trace-1"


def test_any_execution_response_parses_agent_execution_response():
    adapter = TypeAdapter(AnyExecutionResponse)
    parsed = adapter.validate_python(
        {
            "success": True,
            "data": {
                "threadId": "thread-1",
                "runId": "run-1",
                "events": [{"type": "RUN_STARTED"}],
            },
        }
    )

    assert isinstance(parsed, AgentExecutionResponse)
    assert parsed.data.thread_id == "thread-1"


def test_normalize_execution_request_uses_resource_type_to_avoid_union_misclassification():
    normalized_tool = normalize_execution_request(
        resource_type="tool",
        payload={"inputs": {"query": "tool-side-query"}},
    )
    normalized_knowledge = normalize_execution_request(
        resource_type="knowledge",
        payload={"inputs": {"query": "knowledge-side-query"}},
    )
    normalized_agent = normalize_execution_request(
        resource_type="agent",
        payload={
            "inputs": {
                "threadId": "thread-2",
                "runId": "run-2",
                "state": {},
                "messages": [{"id": "u2", "role": "user", "content": "continue"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            }
        },
    )

    assert isinstance(normalized_tool, ToolExecutionRequest)
    assert isinstance(normalized_knowledge, KnowledgeBaseExecutionRequest)
    assert isinstance(normalized_agent, AgentExecutionRequest)
