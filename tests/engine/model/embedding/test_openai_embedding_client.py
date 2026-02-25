# tests/engine/model/embedding/test_openai_embedding_client.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from openai import RateLimitError, AuthenticationError
from openai.types import CreateEmbeddingResponse, Embedding
from openai.types.create_embedding_response import Usage

from app.engine.model.embedding.clients.openai_client import OpenAIEmbeddingClient
from app.engine.model.embedding.base import (
    EmbeddingRateLimitError,
    EmbeddingAuthenticationError,
    BatchEmbeddingResult
)

pytestmark = pytest.mark.asyncio

# --- Helper to create mock API responses ---

def create_mock_embedding_response(vectors: list, total_tokens: int) -> CreateEmbeddingResponse:
    """Creates a mock CreateEmbeddingResponse object using real Pydantic models."""
    embedding_data = [
        Embedding(index=i, embedding=vec, object="embedding")
        for i, vec in enumerate(vectors)
    ]
    return CreateEmbeddingResponse(
        data=embedding_data,
        model="text-embedding-3-small",
        object="list",
        usage=Usage(prompt_tokens=total_tokens, total_tokens=total_tokens)
    )

# --- Fixture for the mocked SDK client ---

@pytest.fixture
def mock_openai_sdk_client(mocker):
    """A fixture that provides a mocked openai.AsyncOpenAI instance."""
    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock()
    mocker.patch("app.engine.model.embedding.clients.openai_client.openai.AsyncOpenAI", return_value=mock_client)
    return mock_client

# --- Test Cases ---

async def test_embed_batch_success(mock_openai_sdk_client, mock_openai_embedding_provider_config, mock_embedding_run_config, mock_texts_input):
    """Test the successful path for batch embedding."""
    # 1. Setup
    mock_vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    mock_response = create_mock_embedding_response(vectors=mock_vectors, total_tokens=5)
    mock_openai_sdk_client.embeddings.create.return_value = mock_response

    client = OpenAIEmbeddingClient(mock_openai_embedding_provider_config)

    # 2. Execute
    result = await client.embed_batch(mock_texts_input, mock_embedding_run_config)

    # 3. Assert
    mock_openai_sdk_client.embeddings.create.assert_called_once()
    assert isinstance(result, BatchEmbeddingResult)
    assert result.total_tokens == 5
    assert len(result.results) == 2
    assert result.results[0].index == 0
    assert result.results[0].vector == [0.1, 0.2, 0.3]
    assert result.results[1].index == 1
    assert result.results[1].vector == [0.4, 0.5, 0.6]

async def test_embed_batch_handles_rate_limit_error(mock_openai_sdk_client, mock_openai_embedding_provider_config, mock_embedding_run_config, mock_texts_input):
    """Test that RateLimitError from the SDK is converted to our custom error."""
    # 1. Setup
    mock_openai_sdk_client.embeddings.create.side_effect = RateLimitError(
        "Rate limit exceeded", response=MagicMock(), body=None
    )
    client = OpenAIEmbeddingClient(mock_openai_embedding_provider_config)

    # 2. Execute & Assert
    with pytest.raises(EmbeddingRateLimitError, match="OpenAI rate limit exceeded"):
        await client.embed_batch(mock_texts_input, mock_embedding_run_config)

async def test_embed_batch_handles_auth_error(mock_openai_sdk_client, mock_openai_embedding_provider_config, mock_embedding_run_config, mock_texts_input):
    """Test that AuthenticationError from the SDK is converted to our custom error."""
    # 1. Setup
    mock_openai_sdk_client.embeddings.create.side_effect = AuthenticationError(
        "Invalid API key", response=MagicMock(), body=None
    )
    client = OpenAIEmbeddingClient(mock_openai_embedding_provider_config)

    # 2. Execute & Assert
    with pytest.raises(EmbeddingAuthenticationError, match="OpenAI authentication failed"):
        await client.embed_batch(mock_texts_input, mock_embedding_run_config)