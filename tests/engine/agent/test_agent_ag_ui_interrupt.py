import json

import pytest

from app.engine.agent import AgentEngineService, AgentInput, AgentEngineCallbacks, BaseToolExecutor, ToolExecutionInterrupt
from app.engine.model.llm import (
    LLMMessage,
    LLMProviderConfig,
    LLMResult,
    LLMRunConfig,
    LLMToolCallChunk,
    LLMTool,
    LLMToolCall,
    LLMUsage,
)


class _InterruptingLLMEngine:
    def __init__(self, tool_call: LLMToolCall):
        self.tool_call = tool_call

    async def run(self, provider_config, run_config, messages, callbacks):
        await callbacks.on_reasoning_chunk("thinking")
        await callbacks.on_tool_call_chunk(
            LLMToolCallChunk(
                index=0,
                tool_call_id=self.tool_call.id,
                tool_name=self.tool_call.function["name"],
                arguments_delta='{"question":"go?"}',
            )
        )
        await callbacks.on_tool_calls_generated([self.tool_call])
        return LLMResult(
            message=LLMMessage(role="assistant", tool_calls=[self.tool_call.model_dump(mode="json")]),
            usage=LLMUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        )


class _MultiToolLLMEngine:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.round = 0

    async def run(self, provider_config, run_config, messages, callbacks):
        if self.round == 0:
            self.round += 1
            await callbacks.on_reasoning_chunk("same-round-thought")
            await callbacks.on_tool_calls_generated(self.tool_calls)
            return LLMResult(
                message=LLMMessage(
                    role="assistant",
                    tool_calls=[tool_call.model_dump(mode="json") for tool_call in self.tool_calls],
                ),
                usage=LLMUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
            )

        await callbacks.on_chunk_generated("done")
        return LLMResult(
            message=LLMMessage(role="assistant", content="done"),
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


class _ClientSideToolExecutor(BaseToolExecutor):
    async def execute(self, tool_name, tool_args):
        if tool_name == "ask_user_confirm":
            return ToolExecutionInterrupt(payload={"tool_name": tool_name, "tool_args": tool_args})
        return {"ok": True}

    def get_llm_tools(self):
        return [
            LLMTool(
                type="function",
                function={
                    "name": "ask_user_confirm",
                    "description": "Ask user confirmation",
                    "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
                },
            )
        ]

class _RecordingCallbacks(AgentEngineCallbacks):
    def __init__(self):
        self.started = False
        self.reasoning = []
        self.tool_calls = []
        self.tool_call_chunks = []
        self.interrupt_result = None
        self.finished_result = None
        self.cancelled_result = None
        self.error = None
        self.usage = []
        self.checkpoints = []

    async def on_agent_start(self):
        self.started = True

    async def on_tool_calls_generated(self, tool_calls):
        self.tool_calls.extend(tool_calls)

    async def on_agent_step(self, step):
        return None

    async def on_tool_call_chunk_generated(self, chunk):
        self.tool_call_chunks.append(chunk)

    async def on_final_chunk_generated(self, chunk):
        return None

    async def on_reasoning_chunk_generated(self, chunk):
        self.reasoning.append(chunk)

    async def on_agent_finish(self, result):
        self.finished_result = result

    async def on_agent_interrupt(self, result):
        self.interrupt_result = result

    async def on_agent_cancel(self, result):
        self.cancelled_result = result

    async def on_agent_error(self, error):
        self.error = error

    async def on_usage(self, usage):
        self.usage.append(usage)

    async def on_checkpoint_snapshot(self, snapshot):
        self.checkpoints.append(snapshot)


@pytest.mark.asyncio
async def test_agent_engine_interrupts_for_client_side_tools_and_forwards_reasoning():
    tool_call = LLMToolCall(
        id="call-1",
        type="function",
        function={"name": "ask_user_confirm", "arguments": "{\"question\":\"go?\"}"},
    )
    llm_engine = _InterruptingLLMEngine(tool_call)
    tool_executor = _ClientSideToolExecutor()
    callbacks = _RecordingCallbacks()

    result = await AgentEngineService(llm_engine=llm_engine).run(
        agent_input=AgentInput(messages=[LLMMessage(role="user", content="Please continue")]),
        provider_config=LLMProviderConfig(client_name="openai", api_key="dummy"),
        run_config=LLMRunConfig(model="gpt-test", stream=True),
        tool_executor=tool_executor,
        callbacks=callbacks,
    )

    assert callbacks.started is True
    assert callbacks.reasoning == ["thinking"]
    assert len(callbacks.tool_call_chunks) == 1
    assert callbacks.tool_call_chunks[0].arguments_delta == '{"question":"go?"}'
    assert len(callbacks.tool_calls) == 1
    assert callbacks.interrupt_result is not None
    assert callbacks.finished_result is None
    assert result.outcome == "interrupted"
    assert len(result.client_tool_calls) == 1
    assert result.client_tool_calls[0].tool_call_id == "call-1"
    assert result.client_tool_calls[0].name == "ask_user_confirm"
    assert result.client_tool_calls[0].arguments == {"question": "go?"}
    assert result.reasoning_content == "thinking"
    assert result.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_multi_tool_same_round_only_first_step_keeps_thought():
    tool_calls = [
        LLMToolCall(id="call-1", type="function", function={"name": "local_a", "arguments": "{\"x\":1}"}),
        LLMToolCall(id="call-2", type="function", function={"name": "local_b", "arguments": "{\"y\":2}"}),
    ]

    class _ServerToolExecutor(BaseToolExecutor):
        async def execute(self, tool_name, tool_args):
            return {"tool": tool_name, "args": tool_args}

        def get_llm_tools(self):
            return []

    callbacks = _RecordingCallbacks()
    result = await AgentEngineService(llm_engine=_MultiToolLLMEngine(tool_calls)).run(
        agent_input=AgentInput(messages=[LLMMessage(role="user", content="run tools")]),
        provider_config=LLMProviderConfig(client_name="openai", api_key="dummy"),
        run_config=LLMRunConfig(model="gpt-test", stream=True),
        tool_executor=_ServerToolExecutor(),
        callbacks=callbacks,
    )

    assert result.outcome == "completed"
    assert len(result.steps) == 2
    assert result.steps[0].thought == "same-round-thought"
    assert result.steps[1].thought is None


@pytest.mark.asyncio
async def test_agent_engine_resume_checkpoint_restores_stack_and_appends_tool_results():
    local_tool_call = LLMToolCall(
        id="call-local",
        type="function",
        function={"name": "local_a", "arguments": "{\"x\":1}"},
    )
    client_tool_call = LLMToolCall(
        id="call-client",
        type="function",
        function={"name": "ask_user_confirm", "arguments": "{\"question\":\"ship?\"}"},
    )

    class _ResumableLLMEngine:
        def __init__(self):
            self.calls = []

        async def run(self, provider_config, run_config, messages, callbacks):
            self.calls.append([message.model_copy(deep=True) for message in messages])
            call_index = len(self.calls) - 1

            if call_index == 0:
                await callbacks.on_reasoning_chunk("local-round")
                await callbacks.on_tool_calls_generated([local_tool_call])
                return LLMResult(
                    message=LLMMessage(role="assistant", tool_calls=[local_tool_call.model_dump(mode="json")]),
                    usage=LLMUsage(prompt_tokens=3, completion_tokens=1, total_tokens=4),
                )

            if call_index == 1:
                await callbacks.on_reasoning_chunk("client-round")
                await callbacks.on_tool_calls_generated([client_tool_call])
                return LLMResult(
                    message=LLMMessage(role="assistant", tool_calls=[client_tool_call.model_dump(mode="json")]),
                    usage=LLMUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                )

            assert messages[-1].role == "tool"
            assert messages[-1].tool_call_id == "call-client"
            assert json.loads(messages[-1].content) == {"approved": True}
            await callbacks.on_reasoning_chunk("resume-round")
            await callbacks.on_chunk_generated("done")
            return LLMResult(
                message=LLMMessage(role="assistant", content="done"),
                usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    class _MixedToolExecutor(BaseToolExecutor):
        async def execute(self, tool_name, tool_args):
            if tool_name == "ask_user_confirm":
                return ToolExecutionInterrupt(payload={"tool_name": tool_name, "tool_args": tool_args})
            return {"tool": tool_name, "args": tool_args}

        def get_llm_tools(self):
            return []

    llm_engine = _ResumableLLMEngine()
    tool_executor = _MixedToolExecutor()
    initial_callbacks = _RecordingCallbacks()
    engine = AgentEngineService(llm_engine=llm_engine, max_iterations=5)

    interrupted = await engine.run(
        agent_input=AgentInput(messages=[LLMMessage(role="user", content="start")]),
        provider_config=LLMProviderConfig(client_name="openai", api_key="dummy"),
        run_config=LLMRunConfig(model="gpt-test", stream=True),
        tool_executor=tool_executor,
        callbacks=initial_callbacks,
    )

    assert interrupted.outcome == "interrupted"
    interrupt_checkpoint = initial_callbacks.checkpoints[-1]
    assert interrupt_checkpoint.phase == "interrupt"
    assert interrupt_checkpoint.next_iteration == 2
    assert len(interrupt_checkpoint.steps) == 1
    assert interrupt_checkpoint.steps[0].action.id == "call-local"
    assert interrupt_checkpoint.usage.total_tokens == 7

    resumed_callbacks = _RecordingCallbacks()
    resumed = await engine.run(
        agent_input=AgentInput(
            messages=[
                LLMMessage(
                    role="tool",
                    tool_call_id="call-client",
                    content='{"approved": true}',
                )
            ]
        ),
        provider_config=LLMProviderConfig(client_name="openai", api_key="dummy"),
        run_config=LLMRunConfig(model="gpt-test", stream=True),
        tool_executor=tool_executor,
        callbacks=resumed_callbacks,
        resume_checkpoint=interrupt_checkpoint,
    )

    assert resumed.outcome == "completed"
    assert len(resumed.steps) == 1
    assert resumed.steps[0].action.id == "call-local"
    assert resumed.usage.total_tokens == 9
    assert resumed.reasoning_content == "local-roundclient-roundresume-round"
    assert llm_engine.calls[2][-1].tool_call_id == "call-client"
