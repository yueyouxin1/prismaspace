# src/app/engine/model/llm/example/_callbacks.py

from app.engine.model.llm import LLMEngineCallbacks, LLMToolCall, LLMMessage, LLMUsage, LLMResult

class PrintCallbacks(LLMEngineCallbacks):
    """A simple implementation of the callbacks protocol that prints events to the console."""
    
    async def on_start(self) -> None:
        print("\n--- Engine Started ---\n")

    async def on_chunk_generated(self, chunk: str) -> None:
        """Prints text chunks as they are generated."""
        print(chunk, end="", flush=True)
    
    async def on_tool_calls_generated(self, tool_calls: list[LLMToolCall]) -> None:
        """Prints the details of the tool calls requested by the model."""
        print("\n\n--- Tool Calls Requested ---")
        for tool_call in tool_calls:
            print(f"  - ID: {tool_call.id}")
            print(f"    Function: {tool_call.function['name']}")
            print(f"    Arguments: {tool_call.function['arguments']}")
        print("--------------------------\n")

    async def on_success(self, result: LLMResult) -> None:
        """Prints a confirmation when the generation is complete."""
        print(f"\n\n--- Generation Successful ---\n\n{result}")

    async def on_cancel(self, result: LLMResult) -> None:
        """Prints a confirmation when the generation is cancel."""
        print(f"\n\n--- Generation Cancel ---\n\n{result}\n\nEND")

    async def on_error(self, error: Exception) -> None:
        """Prints any errors that occur during the generation."""
        print(f"\n\n--- An Error Occurred ---\n{type(error).__name__}: {error}")
        
    async def on_usage(self, usage: LLMUsage) -> None:
        """Prints the token usage statistics."""
        print(f"\n\n--- Usage Stats ---\nPrompt Tokens: {usage.prompt_tokens}\nCompletion Tokens: {usage.completion_tokens}\nTotal Tokens: {usage.total_tokens}\n-------------------")