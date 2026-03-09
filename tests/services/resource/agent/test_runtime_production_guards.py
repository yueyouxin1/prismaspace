from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine.agent import AgentResult
from app.engine.agent.base import AgentRuntimeCheckpoint, AgentClientToolCall
from app.engine.model.llm import LLMMessage, LLMTool, LLMUsage
from app.schemas.protocol import RunAgentInputExt
from app.schemas.resource.agent.agent_schemas import AgentConfig
from app.services.resource.agent import agent_service as agent_service_module
from app.services.resource.agent.agent_service import AgentService
from app.services.resource.agent.protocol_adapter.base import ProtocolAdaptedRun
from app.services.resource.agent.types.agent import AgentStreamMessageIds
from app.utils.async_generator import AsyncGeneratorManager


pytestmark = pytest.mark.asyncio


async def test_async_execute_delegates_to_runtime_runner(monkeypatch):
    service = object.__new__(AgentService)
    sentinel = object()
    captured = {}

    class FakeRunner:
        def __init__(self, *, base_context, db_session_factory):
            captured["base_context"] = base_context
            captured["db_session_factory"] = db_session_factory

        async def start(self, **kwargs):
            captured["start_kwargs"] = kwargs
            return sentinel

    from app.services.resource.agent import runtime_runner as runtime_runner_module

    monkeypatch.setattr(runtime_runner_module, "AgentRuntimeRunner", FakeRunner)

    service.context = SimpleNamespace(name="ctx")
    service._db_session_factory = object()

    result = await service.async_execute(
        instance_uuid="agent-1",
        run_input=SimpleNamespace(),
        actor=SimpleNamespace(id=1),
        runtime_workspace=SimpleNamespace(id=9),
    )

    assert result is sentinel
    assert captured["base_context"] is service.context
    assert captured["db_session_factory"] is service._db_session_factory
    assert captured["start_kwargs"]["instance_uuid"] == "agent-1"
    assert captured["start_kwargs"]["runtime_workspace"].id == 9


async def test_background_task_locks_preflight_and_commits_before_terminal_event(monkeypatch):
    service = object.__new__(AgentService)
    order: list[str] = []
    lock_state = {"held": False}
    captured = {"history": None}

    @asynccontextmanager
    async def fake_lock(_session_uuid: str):
        lock_state["held"] = True
        try:
            yield
        finally:
            lock_state["held"] = False

    class FakePipelineManager:
        def __init__(self, *, system_message, user_message, history, tool_executor):
            captured["history"] = history
            self.user_message = user_message
            self.tool_executor = tool_executor

        def add_standard_processors(self, **kwargs):
            return self

        async def build_context(self):
            assert lock_state["held"] is True
            return [LLMMessage(role="user", content="hi")]

        async def build_skill(self):
            assert lock_state["held"] is True
            return []

    class FakeSpan:
        def __init__(self):
            self.attributes = None

        def set_output(self, _result):
            return None

    class FakeTraceManager:
        force_trace_id = "trace-1"

        async def __aenter__(self):
            return FakeSpan()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    async def fake_preload(_turns: int):
        assert lock_state["held"] is True

    async def fake_mark_running(*args, **kwargs):
        return None

    async def fake_mark_finished(*args, **kwargs):
        order.append("mark_finished")

    async def fake_commit():
        order.append("db.commit")

    async def fake_emit(self):
        order.append("emit_terminal")

    monkeypatch.setattr(agent_service_module, "AgentPipelineManager", FakePipelineManager)
    monkeypatch.setattr(
        agent_service_module.PersistingAgentCallbacks,
        "emit_prepared_terminal_event",
        fake_emit,
    )

    service._session_lock = fake_lock
    service.db = SimpleNamespace(
        refresh=AsyncMock(),
        commit=AsyncMock(side_effect=fake_commit),
        rollback=AsyncMock(),
    )
    service.context = SimpleNamespace(actor=SimpleNamespace(id=42))
    service.agent_memory_var_service = SimpleNamespace(
        get_runtime_object=AsyncMock(return_value={})
    )
    service.prompt_template = SimpleNamespace(render=lambda template, variables: template)
    service.module_service = SimpleNamespace(
        get_runtime_context=AsyncMock(
            return_value=SimpleNamespace(
                version=SimpleNamespace(name="gpt-test", attributes={"context_window": 2048})
            )
        )
    )
    class FakeAIProvider:
        def __init__(self):
            self.execute_agent_with_billing = AsyncMock(
                return_value=AgentResult(
                    message=LLMMessage(role="assistant", content="done"),
                    steps=[],
                    usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                    outcome="completed",
                )
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    service.ai_provider = FakeAIProvider()
    service.execution_ledger_service = SimpleNamespace(
        mark_running=AsyncMock(side_effect=fake_mark_running),
        mark_finished=AsyncMock(side_effect=fake_mark_finished),
    )

    session_manager = SimpleNamespace(
        session=SimpleNamespace(uuid="session-1", turn_count=1),
        preload_recent_messages=AsyncMock(side_effect=fake_preload),
        get_recent_messages=AsyncMock(return_value=[]),
        commit=AsyncMock(),
        buffer_message=lambda **kwargs: None,
        dispatch_post_commit_jobs=AsyncMock(),
        clear_post_commit_jobs=lambda: None,
    )

    adapted = ProtocolAdaptedRun(
        input_content="hello",
        thread_id="session-1",
        client_tools=[],
        custom_history=[LLMMessage(role="system", content="custom-history")],
        resume_messages=[],
        has_custom_history=True,
    )

    await service._run_agent_background_task(
        agent_config=AgentConfig(),
        llm_module_version=SimpleNamespace(id=1),
        runtime_workspace=SimpleNamespace(id=9),
        trace_manager=FakeTraceManager(),
        generator_manager=AsyncGeneratorManager(),
        execution=SimpleNamespace(run_id="run-1"),
        turn_id="turn-1",
        session_manager=session_manager,
        run_input=None,
        message_ids=AgentStreamMessageIds(
            user_message_id="user-1",
            assistant_message_id="assistant-1",
            reasoning_message_id="reasoning-1",
            activity_message_id="activity-1",
        ),
        dependencies=[],
        adapted=adapted,
        tool_executor=SimpleNamespace(),
        agent_instance=SimpleNamespace(version_id=7, system_prompt="system"),
    )

    session_manager.preload_recent_messages.assert_awaited_once()
    session_manager.commit.assert_awaited_once()
    session_manager.dispatch_post_commit_jobs.assert_awaited_once()
    assert captured["history"][0].content == "custom-history"
    assert order == ["mark_finished", "db.commit", "emit_terminal"]


async def test_background_task_passes_resume_messages_as_delta_with_canonical_checkpoint():
    service = object.__new__(AgentService)
    captured = {}

    class FakeTraceManager:
        force_trace_id = "trace-1"

        async def __aenter__(self):
            return SimpleNamespace(attributes=None, set_output=lambda _result: None)

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    class FakeAIProvider:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def execute_agent_with_billing(self, **kwargs):
            captured["agent_input"] = kwargs["agent_input"]
            captured["resume_checkpoint"] = kwargs.get("resume_checkpoint")
            result = AgentResult(
                message=LLMMessage(role="assistant", content="resumed"),
                steps=[],
                usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                outcome="completed",
            )
            await kwargs["callbacks"].on_agent_finish(result)
            return result

    resume_checkpoint = AgentRuntimeCheckpoint(
        phase="interrupt",
        messages=[
            LLMMessage(role="user", content="hello"),
            LLMMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "ask_user_confirm", "arguments": "{\"question\":\"go?\"}"},
                    }
                ],
            ),
        ],
        tools=[
            LLMTool(
                type="function",
                function={
                    "name": "ask_user_confirm",
                    "description": "Ask user confirmation",
                    "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
                },
            )
        ],
        pending_client_tool_calls=[
            AgentClientToolCall(tool_call_id="call-1", name="ask_user_confirm", arguments={"question": "go?"})
        ],
        next_iteration=1,
        reasoning_content="waiting",
    )

    run_input = RunAgentInputExt.model_validate(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [{"id": "a1", "role": "assistant", "content": "waiting"}],
            "tools": [],
            "context": [],
            "forwardedProps": {"platform": {"sessionMode": "stateless"}},
        }
    ).model_copy(update={"parent_run_id": "parent-1"})

    service.ai_provider = FakeAIProvider()
    service.context = SimpleNamespace(actor=SimpleNamespace(id=42))
    service.module_service = SimpleNamespace(
        get_runtime_context=AsyncMock(
            return_value=SimpleNamespace(
                version=SimpleNamespace(name="gpt-test", attributes={"context_window": 2048})
            )
        )
    )
    service.execution_ledger_service = SimpleNamespace(
        get_by_run_id=AsyncMock(return_value=SimpleNamespace(id=99)),
        mark_running=AsyncMock(),
        mark_finished=AsyncMock(),
    )
    service.run_persistence_service = SimpleNamespace(
        get_checkpoint=AsyncMock(
            return_value=SimpleNamespace(
                runtime_snapshot=resume_checkpoint.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
        )
    )
    service.live_event_service = SimpleNamespace(record_event=AsyncMock())
    service.db = SimpleNamespace(
        refresh=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service._session_lock = AsyncMock()
    service._should_cancel_run = AsyncMock(return_value=False)
    service._clear_cancel_run = AsyncMock()
    service._delete_run_checkpoint = AsyncMock()
    service._upsert_run_checkpoint = AsyncMock()
    service._persist_agent_run_artifacts = AsyncMock()
    service._resolve_model_context_window = AgentService._resolve_model_context_window
    service._restore_runtime_checkpoint = AgentService._restore_runtime_checkpoint
    service.build_interrupt_id = AgentService.build_interrupt_id

    adapted = ProtocolAdaptedRun(
        input_content="",
        thread_id="thread-1",
        client_tools=[],
        custom_history=[],
        resume_messages=[
            LLMMessage(
                role="tool",
                tool_call_id="call-1",
                content='{"approved": true}',
            )
        ],
        has_custom_history=False,
        resume_tool_call_ids=["call-1"],
        resume_interrupt_id="parent-1",
    )

    await service._run_agent_background_task(
        agent_config=AgentConfig(),
        llm_module_version=SimpleNamespace(id=1),
        runtime_workspace=SimpleNamespace(id=9),
        trace_manager=FakeTraceManager(),
        generator_manager=AsyncGeneratorManager(),
        execution=SimpleNamespace(id=1, run_id="run-1", thread_id="thread-1"),
        turn_id="turn-1",
        session_manager=None,
        run_input=run_input,
        message_ids=AgentStreamMessageIds(
            user_message_id="user-1",
            assistant_message_id="assistant-1",
            reasoning_message_id="reasoning-1",
            activity_message_id="activity-1",
        ),
        dependencies=[],
        adapted=adapted,
        tool_executor=SimpleNamespace(),
        agent_instance=SimpleNamespace(version_id=7, system_prompt="system"),
    )

    assert captured["resume_checkpoint"] is not None
    assert captured["resume_checkpoint"].phase == "interrupt"
    assert len(captured["agent_input"].messages) == 1
    assert captured["agent_input"].messages[0].role == "tool"
    assert captured["agent_input"].messages[0].tool_call_id == "call-1"
