# src/app/engine/model/embedding/example/basic_embedding.py

import os
import asyncio
from app.engine.model.embedding import (
    EmbeddingEngineService,
    EmbeddingProviderConfig,
    EmbeddingRunConfig,
)

# --- Configuration ---
# IMPORTANT: Set your OpenAI API key in your environment variables.
API_KEY = "sk-LAdEXTUw5P"
if not API_KEY:
    raise ValueError("API_KEY environment variable not set.")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "text-embedding-v4"
PROVIDER_CONFIG = EmbeddingProviderConfig(client_name="openai", base_url=BASE_URL, api_key=API_KEY)

# Using a modern, cost-effective embedding model.
# The 'dimensions' parameter is optional and only supported by newer models like text-embedding-3-small/large.
# It allows you to truncate the vectors to a smaller size, saving storage and potentially speeding up similarity search.
RUN_CONFIG = EmbeddingRunConfig(
    model=MODEL_NAME,
    # dimensions=256  # Uncomment this line to get smaller vectors
)
# ---------------------

async def main():
    """Demonstrates a simple batch embedding process."""
    print(">>> Running Example: Basic Batch Embedding <<<")

    # 1. Initialize the engine
    engine = EmbeddingEngineService()

    # 2. Define the list of texts to be embedded
    texts_to_embed = [
        "The cat sat on the mat.",
        "A quick brown fox jumps over the lazy dog.",
        "Vector embeddings represent text in a high-dimensional space.",
        "PrismaSpace provides a robust engine architecture."
    ]
    print(f"\nInput texts ({len(texts_to_embed)} total):")
    for i, text in enumerate(texts_to_embed):
        print(f"  {i}: '{text}'")

    # 3. Run the engine
    try:
        print("\n[Engine] Calling the embedding service...")
        result = await engine.run_batch(
            provider_config=PROVIDER_CONFIG,
            run_config=RUN_CONFIG,
            texts=texts_to_embed
        )
        print("[Engine] Call successful. Processing results...")

        # 4. Print the results in a formatted way
        print("\n--- Embedding Results ---")
        print(f"Total Tokens Billed: {result.total_tokens}")
        print(f"Number of Vectors Generated: {len(result.results)}")
        print("-------------------------")
        for res in result.results:
            # Show a small preview of the vector to avoid flooding the console
            vector_preview = f"[{', '.join(map(str, res.vector[:5]))}, ...]"
            print(f"  - Index: {res.index}")
            print(f"    Text: '{texts_to_embed[res.index]}'")
            print(f"    Vector Dimensions: {len(res.vector)}")
            print(f"    Vector Preview: {vector_preview}")
        print("-------------------------\n")


    except Exception as e:
        print(f"\n--- An Error Occurred ---\n{type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())