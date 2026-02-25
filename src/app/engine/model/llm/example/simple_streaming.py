# src/app/engine/model/llm/example/simple_streaming.py

import os
import asyncio
from app.engine.model.llm import (
    LLMEngineService,
    LLMProviderConfig,
    LLMRunConfig,
    LLMMessage,
    LLMResult
)
from ._callbacks import PrintCallbacks

# --- Configuration ---
# IMPORTANT: Set your OpenAI API key in your environment variables.
API_KEY = "sk-LAdEXTUw5P"
if not API_KEY:
    raise ValueError("API_KEY environment variable not set.")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-plus-2025-09-11"
PROVIDER_CONFIG = LLMProviderConfig(client_name="openai", base_url=BASE_URL, api_key=API_KEY)
RUN_CONFIG = LLMRunConfig(model=MODEL_NAME, stream=True, temperature=0.5, response_format={"type": "text"})
# ---------------------

async def main():
    """Demonstrates a simple streaming text generation."""
    print(">>> Running Example 1: Simple Streaming <<<")

    # 1. Initialize the engine and callbacks
    engine = LLMEngineService()
    callbacks = PrintCallbacks()

    # 2. Define the conversation messages
    messages = [
        LLMMessage(role="system", content="You are a helpful assistant."),
        LLMMessage(role="user", content="9.11和9.9哪个大?"),
    ]

    # 3. Run the engine
    try:
        task = asyncio.create_task(engine.run(
            provider_config=PROVIDER_CONFIG,
            run_config=RUN_CONFIG,
            messages=messages,
            callbacks=callbacks
        ))
        #await asyncio.sleep(2)
        #task.cancel()
        result: LLMResult = await task
        print(f"LLM RESULT: {result}")
    except Exception as e:
        print(f"\nAn exception occurred in the engine run: {e}")

if __name__ == "__main__":
    asyncio.run(main())