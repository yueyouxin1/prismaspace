# scripts/test_tool_engine.py

import asyncio
import os
import sys
from typing import Dict, Any, List

# --- [Setup] Add project root to Python path ---
# This allows the script to import modules from the 'app' directory
# by running `python scripts/test_tool_engine.py` from the project root.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
# ---------------------------------------------

# Now we can import our application modules
from app.engine.schemas.parameter_schema import ParameterSchema, SchemaBlueprint, ParameterValue
from app.engine.tool.main import ToolEngineService
from app.engine.tool.callbacks import ToolEngineCallbacks

# ==============================================================================
# 1. DEFINE A TEST TOOL using ParameterSchema
#    We will model a tool that calls the wttr.in weather API.
# ==============================================================================

# The URL for the public weather API. Note the {city} placeholder.
WEATHER_API_URL = "https://wttr.in/{city}"

# Define the input parameters for our weather tool
WEATHER_INPUT_SCHEMA: List[ParameterSchema] = [
    # Parameter 1: The city name, provided by the user at runtime.
    ParameterSchema(
        name="city",
        type="string",
        required=True,
        open=True,
        label="City Name",
        description="The name of the city to get the weather for (e.g., 'London', 'Shanghai').",
        role="http.path"  # Crucial: Tells the engine to place this value in the URL path.
    ),
    ParameterSchema(
        name="format",
        type="string",
        required=False,
        open=False, # Hidden from the end-user/LLM.
        role="http.query", # Crucial: Tells the engine to use this as a URL query parameter.
        default="j1"
    )
]

# Define the expected output structure of the tool.
# We don't need to model the entire API response, only the parts we want to extract and validate.
WEATHER_OUTPUT_SCHEMA: List[ParameterSchema] = [
    ParameterSchema(
        name="current_condition",
        type="array",
        required=True,
        description="An array containing the current weather conditions.",
        items=SchemaBlueprint(
            type="object",
            # The properties *inside* the object are named, so they use ParameterSchema.
            properties=[
                ParameterSchema(name="temp_C", type="string", required=True, description="Temperature in Celsius."),
                ParameterSchema(name="humidity", type="string", required=True, description="Humidity percentage."),
                ParameterSchema(
                    name="weatherDesc",
                    type="array",
                    required=True,
                    items=SchemaBlueprint(
                        type="object",
                        properties=[ParameterSchema(name="value", type="string", required=True)]
                    )
                )
            ]
        )
    )
]


# ==============================================================================
# 2. IMPLEMENT A CONCRETE CALLBACK for logging to the console
# ==============================================================================

class ConsoleLoggerCallbacks(ToolEngineCallbacks):
    """A simple implementation of the callbacks protocol that prints events."""

    async def on_start(self, context: Dict[str, Any], inputs: Dict[str, Any]) -> None:
        print("\n" + "="*50)
        print(f"🚀 [START] Execution initiated. Context: {context}")
        print(f"📥 [INPUTS] Runtime arguments received: {inputs}")
        print("─"*50)

    async def on_log(self, message: str, metadata: Dict[str, Any] = None) -> None:
        print(f"📝 [LOG] {message}")
        if metadata:
            import json
            # Pretty-print metadata for readability
            print(f"  └─ Metadata: {json.dumps(metadata, indent=2)}")

    async def on_success(self, result: Dict[str, Any], raw_response: Any) -> None:
        print("─"*50)
        print("✅ [SUCCESS] Execution completed successfully.")
        import json
        print(f"✨ [SHAPED RESULT] The final, structured output is:\n{json.dumps(result, indent=2)}")
        print("="*50 + "\n")

    async def on_error(self, error: Exception) -> None:
        print("─"*50)
        print(f"❌ [ERROR] An error occurred during execution: {type(error).__name__}")
        print(f"  └─ Details: {error}")
        print("="*50 + "\n")


# ==============================================================================
# 3. THE MAIN TEST FUNCTION
# ==============================================================================

async def main():
    """Orchestrates the independent test of the ToolEngineService."""
    print(">>> Starting independent test of ToolEngineService <<<")

    # Instantiate the engine and our console logger
    engine = ToolEngineService()
    callbacks = ConsoleLoggerCallbacks()

    # Define the runtime arguments as if they came from a user
    runtime_args = {"city": "Shanghai"}
    
    # Define an execution context, similar to what the ExecutionService would provide
    exec_context = {"trace_id": "standalone-test-12345", "user_uuid": "user-abcde"}

    try:
        
        final_result = await engine.run(
            method="GET",
            url=WEATHER_API_URL,
            inputs_schema=WEATHER_INPUT_SCHEMA,
            outputs_schema=WEATHER_OUTPUT_SCHEMA,
            runtime_arguments=runtime_args,
            callbacks=callbacks,
            execution_context=exec_context
        )
        
        # You can add assertions here if running this within a test framework like pytest
        # For a standalone script, visual confirmation is sufficient.
        assert "current_condition" in final_result
        assert isinstance(final_result["current_condition"], list)
        assert "temp_C" in final_result["current_condition"][0]

    except Exception as e:
        # The callbacks will have already logged the error, but we catch it
        # here to prevent the script from crashing.
        print(f"High-level catch: The engine run failed. Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())