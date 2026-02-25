# tests/engine/agent/test_agent_engine_service.py
import pytest
import json
from unittest.mock import AsyncMock
from app.engine.agent import AgentEngineService
from app.engine.model.llm import LLMMessage, LLMToolCall

pytestmark = pytest.mark.asyncio

async def test_full_cycle_single_tool_use(
    mock_llm_engine, mock_tool_executor, mock_agent_callbacks,
    mock_provider_config, mock_run_config, mock_initial_agent_input
):
    """
    Test the complete "happy path":
    1. LLM asks to use a tool.
    2. Agent executes the tool.
    3. Agent sends result back to LLM.
    4. LLM provides the final answer.
    """
    # 1. Setup
    tool_call = LLMToolCall(
        id="call_123", type="function",
        function={"name": "get_weather", "arguments": '{"location": "Beijing"}'}
    )
    tool_result = {"temperature": "15°C"}
    final_answer = LLMMessage(role="assistant", content="The weather in Beijing is 15°C.")

    mock_llm_engine.set_responses([
        ("tool_calls", [tool_call]),
        ("final_answer", final_answer)
    ])
    mock_tool_executor.execute_mock.return_value = tool_result
    
    agent_engine = AgentEngineService(llm_engine=mock_llm_engine, tool_executor=mock_tool_executor)

    # 2. Execute
    output = await agent_engine.run(
        agent_input=mock_initial_agent_input,
        callbacks=mock_agent_callbacks,
        provider_config=mock_provider_config,
        run_config=mock_run_config
    )

    # 3. Assert
    # Assert callbacks
    mock_agent_callbacks.on_agent_start.assert_called_once()
    mock_agent_callbacks.on_agent_step.assert_called_once()
    mock_agent_callbacks.on_final_chunk_generated.assert_called_once_with("The weather in Beijing is 15°C.")
    mock_agent_callbacks.on_agent_finish.assert_called_once()
    
    # Assert tool execution
    mock_tool_executor.execute_mock.assert_called_once_with("get_weather", {"location": "Beijing"})

    # Assert LLM calls
    assert len(mock_llm_engine.call_history) == 2
    # First call only has the user message
    assert len(mock_llm_engine.call_history[0]) == 1
    # Second call has user, assistant (tool_call), and tool (result) messages
    assert len(mock_llm_engine.call_history[1]) == 3
    assert mock_llm_engine.call_history[1][-1].role == "tool"
    assert json.loads(mock_llm_engine.call_history[1][-1].content) == tool_result
    
    # Assert final output
    assert output.final_answer.content == "The weather in Beijing is 15°C."
    assert len(output.intermediate_steps) == 1
    assert output.intermediate_steps[0].observation == tool_result

async def test_context_processor_is_called(
    mock_llm_engine, mock_tool_executor, mock_context_processor, mock_agent_callbacks,
    mock_provider_config, mock_run_config, mock_initial_agent_input
):
    """Test that the context processor is called before the main loop."""
    # 1. Setup
    final_answer = LLMMessage(role="assistant", content="Done.")
    mock_llm_engine.set_responses([("final_answer", final_answer)])
    
    # Configure mock processor to add a system message
    processed_messages = [LLMMessage(role="system", content="Processed.")] + mock_initial_agent_input.messages
    mock_context_processor.process_mock.side_effect = AsyncMock(return_value=processed_messages)

    agent_engine = AgentEngineService(
        llm_engine=mock_llm_engine, 
        tool_executor=mock_tool_executor,
        context_processors=[mock_context_processor]
    )
    
    # 2. Execute
    await agent_engine.run(
        agent_input=mock_initial_agent_input,
        callbacks=mock_agent_callbacks,
        provider_config=mock_provider_config,
        run_config=mock_run_config
    )
    
    # 3. Assert
    mock_context_processor.process_mock.assert_called_once_with(mock_initial_agent_input.messages)
    # Verify that the LLM received the *processed* messages
    assert mock_llm_engine.call_history[0] == processed_messages

async def test_max_iterations_reached(
    mock_llm_engine, mock_tool_executor, mock_agent_callbacks,
    mock_provider_config, mock_run_config, mock_initial_agent_input
):
    """Test that the agent stops if it exceeds max_iterations."""
    # 1. Setup
    # Configure the LLM to always ask for a tool
    tool_call = LLMToolCall(id="call_1", type="function", function={"name": "foo", "arguments": "{}"})
    mock_llm_engine.set_responses([
        ("tool_calls", [tool_call]),
        ("tool_calls", [tool_call]),
        ("tool_calls", [tool_call]), # More responses than iterations
    ])
    
    # Set a low iteration limit
    agent_engine = AgentEngineService(
        llm_engine=mock_llm_engine, tool_executor=mock_tool_executor, max_iterations=2
    )
    
    # 2. Execute & Assert
    with pytest.raises(Exception, match="Agent reached maximum iterations"):
        await agent_engine.run(
            agent_input=mock_initial_agent_input,
            callbacks=mock_agent_callbacks,
            provider_config=mock_provider_config,
            run_config=mock_run_config
        )
    
    # Verify it tried to loop exactly max_iterations times
    assert mock_tool_executor.execute_mock.call_count == 2
    mock_agent_callbacks.on_agent_error.assert_called_once()