# src/app/engine/agent/example/full_cycle_with_rag.py

import os
import asyncio
import json
from typing import List
from app.engine.agent import (
    AgentEngineService,
    AgentInput,
    AgentStep,
    AgentResult,
    BaseToolExecutor,
    AgentEngineCallbacks
)
from app.engine.model.llm import (
    LLMEngineService,
    LLMProviderConfig,
    LLMRunConfig,
    LLMMessage,
    LLMTool,
    LLMToolCall,
    LLMUsage
)

# --- Configuration ---
API_KEY = "sk-LAdEXTUw5P"
if not API_KEY:
    raise ValueError("API_KEY environment variable not set.")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-plus-2025-09-11"
PROVIDER_CONFIG = LLMProviderConfig(client_name="openai", base_url=BASE_URL, api_key=API_KEY)
# ---------------------

# --- 1. CONCRETE IMPLEMENTATIONS of the Agent's "Plugins" ---

class MyToolExecutor(BaseToolExecutor):
    """
    A concrete implementation of the tool executor protocol.
    This class knows how to execute a predefined set of tools.
    """
    def _get_weather(self, location: str, unit: str = "celsius"):
        """A mock tool that returns static weather data."""
        print(f"\n[Tool] Executing get_weather(location='{location}', unit='{unit}')")
        if "tokyo" in location.lower():
            return {"temperature": "10", "condition": "Cloudy"}
        return {"temperature": "25", "condition": "Sunny"}

    def _get_stock_price(self, ticker: str):
        """A mock tool that returns a static stock price."""
        print(f"\n[Tool] Executing get_stock_price(ticker='{ticker}')")
        if ticker == "PRISMA":
            return {"price": 125.50, "currency": "USD"}
        return {"price": "unknown", "currency": "USD"}
        
    async def execute(self, tool_name: str, tool_args: dict) -> dict:
        if tool_name == "get_weather":
            return self._get_weather(**tool_args)
        elif tool_name == "get_stock_price":
            return self._get_stock_price(**tool_args)
        else:
            return {"error": f"Tool '{tool_name}' not found."}

    def get_llm_tools(self) -> List[LLMTool]:
        # These definitions must match the implementations in MyToolExecutor
        WEATHER_TOOL = LLMTool(function={
            "name": "get_weather", "description": "Get the current weather in a location.",
            "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}
        })
        STOCK_TOOL = LLMTool(function={
            "name": "get_stock_price", "description": "Get the latest stock price for a ticker symbol.",
            "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}
        })
        return [WEATHER_TOOL, STOCK_TOOL]


class MyRAGProcessor:
    """
    A concrete implementation of the context processor protocol.
    This simulates a RAG (Retrieval-Augmented Generation) step by injecting
    relevant information into the context before the LLM sees it.
    """
    async def process(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        last_user_message = next((m.content for m in reversed(messages) if m.role == 'user'), "")
        
        # Simulate a knowledge base lookup
        if "primaspace" in last_user_message.lower():
            print("\n[RAG] Found relevant context for 'Primaspace'. Injecting into context...")
            retrieved_context = (
                "Primaspace Inc. is a fictional company that develops advanced AI engine architectures. "
                "Its stock ticker is PRISMA."
            )
            rag_message = LLMMessage(role="system", content=f"CONTEXT: {retrieved_context}")
            # Prepend the retrieved context to the message history
            return [rag_message] + messages
            
        print("\n[RAG] No relevant context found. Proceeding without injection.")
        return messages


class MyAgentCallbacks(AgentEngineCallbacks):
    """
    A concrete implementation of the agent callbacks to provide observability.
    """
    async def on_agent_start(self) -> None:
        print("\n--- Agent Run Started ---")

    async def on_agent_step(self, step: AgentStep) -> None:
        """This is the most important callback for observing the ReAct loop."""
        print("\n--- Agent Step Completed ---")
        print(f"  Action: Call tool '{step.action.function['name']}'")
        print(f"  Arguments: {step.action.function['arguments']}")
        print(f"  Observation: {json.dumps(step.observation)}")
        print("--------------------------")

    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]) -> None:
        pass

    async def on_final_chunk_generated(self, chunk: str) -> None:
        """Stream the final answer to the console."""
        print(chunk, end="", flush=True)

    async def on_agent_finish(self, result: AgentResult) -> None:
        print("\n\n--- Agent Run Finished ---")
        print(f"Total Steps: {len(result.steps)}")
        print("--------------------------")

    async def on_agent_cancel(self, result: AgentResult) -> None:
        print("\n\n--- Agent Run Cancel ---")
        print(f"Total Steps: {len(result.steps)}")
        print("--------------------------")

    async def on_agent_error(self, error: Exception) -> None:
        print(f"\n\n--- Agent Error ---\n{type(error).__name__}: {error}")

    async def on_usage(self, usage: LLMUsage) -> None:
        print(f"\n\n--- Step Usage ---\n{usage}")

async def main():
    """
    Main function to set up and run the AgentEngineService.
    """
    print(">>> Running Example: Full Agent Cycle with RAG and Tools <<<")

    # --- 2. DEFINE the tools the LLM can use ---

    # --- 3. INSTANTIATE all components ---
    llm_engine = LLMEngineService()
    my_tool_executor = MyToolExecutor()
    my_rag_processor = MyRAGProcessor()
    my_agent_callbacks = MyAgentCallbacks()

    # --- 4. INITIALIZE the Agent Engine, injecting its dependencies ---
    agent_engine = AgentEngineService(
        llm_engine=llm_engine
    )

    # --- 5. PREPARE the inputs for the agent run ---
    # This prompt is designed to trigger both the RAG processor and multiple tool calls
    user_prompt = "What is the stock price for Primaspace Inc., and what's the weather in Tokyo?"
    
    messages = await my_rag_processor.process([
        LLMMessage(role="user", content=user_prompt)
    ])
    agent_input = AgentInput(messages=messages)
    
    run_config = LLMRunConfig(
        model=MODEL_NAME,
        tools=my_tool_executor.get_llm_tools()
    )

    # --- 6. RUN the agent ---
    try:
        result: AgentResult = await agent_engine.run(
            agent_input=agent_input,
            provider_config=PROVIDER_CONFIG,
            run_config=run_config,
            callbacks=my_agent_callbacks,
            tool_executor=my_tool_executor
        )
        print(f"Agent RESULT: {result}")
    except Exception as e:
        print(f"\nAn exception occurred in the agent run: {e}")


if __name__ == "__main__":
    asyncio.run(main())