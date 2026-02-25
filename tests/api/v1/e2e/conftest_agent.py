# tests/api/v1/e2e/conftest_agent.py

import pytest
from unittest.mock import AsyncMock
from app.engine.model.llm import LLMEngineService, LLMMessage, LLMUsage, LLMToolCall, LLMResult

@pytest.fixture
def mock_llm_engine_service(monkeypatch):
    """
    Mock LLM 引擎服务。
    模拟真实的流式输出、工具调用和用量统计。
    """
    mock_service = AsyncMock(spec=LLMEngineService)
    
    # 用于存储捕获到的输入消息，供断言使用
    captured_inputs = []
    
    # 剧本：List[Tuple[type, content]]
    # type: "text" | "tool"
    # content: str (text) | dict (tool call info)
    response_sequence = [] 

    async def run_mock(provider_config, run_config, messages, callbacks) -> LLMResult:
        # 1. 捕获输入
        captured_inputs.append(messages)
        
        await callbacks.on_start()
        
        # 2. 根据剧本生成响应
        if response_sequence:
            response_type, content = response_sequence.pop(0)
        else:
            # 默认兜底响应
            response_type = "text"
            content = "I am a mock AI."

        final_usage = LLMUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        final_result = LLMResult(message=LLMMessage(role="assistant", content=content), usage=final_usage)

        if response_type == "text":
            # 模拟流式输出 (分两块发送)
            chunk_size = max(1, len(content) // 2)
            await callbacks.on_chunk_generated(content[:chunk_size])
            await callbacks.on_chunk_generated(content[chunk_size:])
            
            # [关键] 必须调用 on_success，Agent Service 依赖此信号记录历史
            await callbacks.on_success(final_result)
            
        elif response_type == "tool":
            # 模拟工具调用
            import json
            # 构造工具调用对象
            tool_calls = [
                LLMToolCall(
                    id="call_mock_123",
                    type="function",
                    function={
                        "name": content["name"], 
                        "arguments": json.dumps(content.get("arguments", {}))
                    }
                )
            ]
            # 触发工具调用回调
            await callbacks.on_tool_calls_generated(tool_calls)
            
            # 模拟 LLM 返回 tool_calls 消息
            tool_msg = LLMMessage(role="assistant", tool_calls=[tc.model_dump() for tc in tool_calls])
            await callbacks.on_success(tool_msg)

        # 3. 模拟计费 (必须触发，否则计费测试无法通过)
        await callbacks.on_usage(final_usage)

        return final_result

    mock_service.run.side_effect = run_mock
    mock_service.captured_inputs = captured_inputs
    mock_service.response_sequence = response_sequence
    
    return mock_service