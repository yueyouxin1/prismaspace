import pytest

from app.engine.agent import AgentEngineService, AgentInput, AgentEngineCallbacks, BaseToolExecutor
from app.engine.model.llm import (
    LLMMessage,
    LLMProviderConfig,
    LLMResult,
    LLMRunConfig,
    LLMTool,
    LLMToolCall,
    LLMUsage,
)


class _InterruptingLLMEngine:
    def __init__(self, tool_call: LLMToolCall):
        self.tool_call = tool_call

    async def run(self, provider_config, run_config, messages, callbacks):
        await callbacks.on_reasoning_chunk("thinking")
        await callbacks.on_tool_calls_generated([self.tool_call])
        return LLMResult(
            message=LLMMessage(role="assistant", tool_calls=[self.tool_call.model_dump(mode="json")]),
            usage=LLMUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        )


class _ClientSideToolExecutor(BaseToolExecutor):
    async def execute(self, tool_name, tool_args):
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

    def requires_client_execution(self, tool_name: str) -> bool:
        return tool_name == "ask_user_confirm"


class _RecordingCallbacks(AgentEngineCallbacks):
    def __init__(self):
        self.started = False
        self.reasoning = []
        self.tool_calls = []
        self.interrupt_result = None
        self.finished_result = None
        self.cancelled_result = None
        self.error = None
        self.usage = []

    async def on_agent_start(self):
        self.started = True

    async def on_tool_calls_generated(self, tool_calls):
        self.tool_calls.extend(tool_calls)

    async def on_agent_step(self, step):
        return None

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
    assert len(callbacks.tool_calls) == 1
    assert callbacks.interrupt_result is not None
    assert callbacks.finished_result is None
    assert result.outcome == "interrupted"
    assert len(result.pending_tool_calls) == 1
    assert result.pending_tool_calls[0].id == "call-1"
    assert result.usage.total_tokens == 5
