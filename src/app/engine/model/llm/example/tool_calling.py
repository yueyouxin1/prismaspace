# src/app/engine/model/llm/example/tool_calling.py

import os
import asyncio
import json
from app.engine.model.llm import (
    LLMEngineService,
    LLMProviderConfig,
    LLMRunConfig,
    LLMMessage,
    LLMTool,
    LLMToolCall,
    LLMResult,
    LLMEngineCallbacks, # We'll create a custom callback handler for this
)
from ._callbacks import PrintCallbacks # We can inherit from it to reduce boilerplate

# --- Configuration ---
API_KEY = "sk-LAdEXTUw5P"
if not API_KEY:
    raise ValueError("API_KEY environment variable not set.")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-plus-2025-09-11"
PROVIDER_CONFIG = LLMProviderConfig(client_name="openai", base_url=BASE_URL, api_key=API_KEY)

# Define the tool the model can use
WEATHER_TOOL = LLMTool(
    function={
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "The city and state, e.g., San Francisco, CA"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["location"],
        },
    }
)
RUN_CONFIG = LLMRunConfig(model=MODEL_NAME, stream=True, tools=[WEATHER_TOOL])
# ---------------------

class ToolOrchestrator(PrintCallbacks):
    """
    An advanced callback handler that simulates an agentic loop.
    It handles tool calls by executing them and re-invoking the engine.
    """
    def __init__(self, engine: LLMEngineService):
        self.engine = engine
        self.message_history = []
        self._loop_finished = asyncio.Event()

    def _get_current_weather(self, location: str, unit: str = "celsius") -> dict:
        """A mock tool implementation."""
        print(f"\n[Tool Execution] Getting weather for {location} in {unit}...")
        if "tokyo" in location.lower():
            return {"location": "Tokyo", "temperature": "10", "unit": unit}
        elif "san francisco" in location.lower():
            return {"location": "San Francisco", "temperature": "72", "unit": unit}
        else:
            return {"location": location, "temperature": "unknown", "unit": unit}

    async def on_success(self, final_result: LLMResult) -> None:
        """Override on_success to stop the loop only on a final text answer."""
        if not final_result.message.tool_calls:
            await super().on_success(final_result)
            self.message_history.append(final_result.message)
            self._loop_finished.set()

    async def on_tool_calls_generated(self, tool_calls: list[LLMToolCall]) -> None:
        """The core orchestration logic."""
        await super().on_tool_calls_generated(tool_calls)
        
        # Step 1: Add the assistant's tool-call request to history
        assistant_message = LLMMessage(role="assistant", tool_calls=[tc.model_dump() for tc in tool_calls])
        self.message_history.append(assistant_message)

        # Step 2: Execute tools and collect results
        for tool_call in tool_calls:
            function_name = tool_call.function['name']
            arguments = json.loads(tool_call.function['arguments'])
            
            if function_name == "get_current_weather":
                result = self._get_current_weather(**arguments)
            else:
                result = {"error": f"Unknown tool: {function_name}"}

            # Step 3: Add the tool's result to history
            self.message_history.append(
                LLMMessage(role="tool", tool_call_id=tool_call.id, content=json.dumps(result))
            )
        
        # Step 4: Re-call the engine with the updated history
        print("\n[Orchestrator] Re-calling engine with tool results...")
        await self.engine.run(
            provider_config=PROVIDER_CONFIG,
            run_config=RUN_CONFIG,
            messages=self.message_history,
            callbacks=self
        )

    async def wait_for_completion(self):
        """Waits until the loop is explicitly finished."""
        await self._loop_finished.wait()


async def main():
    """Demonstrates a full tool-calling loop."""
    print("\n>>> Running Example 2: Tool Calling <<<")
    
    engine = LLMEngineService()
    orchestrator = ToolOrchestrator(engine)
    
    messages = [
        LLMMessage(role="user", content="What's the weather like in San Francisco?")
    ]
    orchestrator.message_history = messages.copy()
    
    try:
        # Initial call to the engine
        await engine.run(
            provider_config=PROVIDER_CONFIG,
            run_config=RUN_CONFIG,
            messages=orchestrator.message_history,
            callbacks=orchestrator
        )
        # Wait for the entire multi-step process to finish
        await orchestrator.wait_for_completion()
    except Exception as e:
        print(f"\nAn exception occurred in the engine run: {e}")


if __name__ == "__main__":
    asyncio.run(main())