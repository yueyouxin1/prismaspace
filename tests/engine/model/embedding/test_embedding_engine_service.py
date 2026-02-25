# tests/engine/model/embedding/test_embedding_engine_service.py
import pytest
from unittest.mock import AsyncMock

from app.engine.model.embedding import EmbeddingEngineService, EmbeddingProviderConfig
from app.engine.model.embedding.base import (
    BaseEmbeddingClient,
    BatchEmbeddingResult,
    EmbeddingEngineError,
    EmbeddingProviderNotFoundError
)

pytestmark = pytest.mark.asyncio

class MockEmbeddingClient(BaseEmbeddingClient):
    """A mock client for testing the engine service."""
    def __init__(self, config):
        self.embed_batch_mock = AsyncMock()

    async def embed_batch(self, texts, run_config):
        return await self.embed_batch_mock(texts, run_config)


async def test_engine_selects_correct_client(mocker, mock_openai_embedding_provider_config, mock_embedding_run_config, mock_texts_input):
    """Test that the engine correctly selects and uses the client based on client_name."""
    # 1. Setup
    engine = EmbeddingEngineService()
    mock_client_instance = MockEmbeddingClient(mock_openai_embedding_provider_config)
    mock_client_instance.embed_batch_mock.return_value = BatchEmbeddingResult(results=[], total_tokens=10)
    mocker.patch(
        "app.engine.model.embedding.main.EmbeddingEngineService._get_client",
        return_value=mock_client_instance
    )

    # 2. Execute
    await engine.run_batch(
        provider_config=mock_openai_embedding_provider_config,
        run_config=mock_embedding_run_config,
        texts=mock_texts_input
    )

    # 3. Assert
    engine._get_client.assert_called_once_with(mock_openai_embedding_provider_config)
    mock_client_instance.embed_batch_mock.assert_called_once()

async def test_engine_handles_unknown_provider(mock_embedding_run_config, mock_texts_input):
    """Test that the engine raises an error for an unregistered provider."""
    # 1. Setup
    engine = EmbeddingEngineService()
    unknown_config = EmbeddingProviderConfig(client_name="unknown", api_key="fake")

    # 2. Execute & Assert
    with pytest.raises(EmbeddingProviderNotFoundError, match="No Embedding client registered for provider 'unknown'"):
        await engine.run_batch(unknown_config, mock_embedding_run_config, mock_texts_input)

async def test_engine_handles_empty_text_list(mock_openai_embedding_provider_config, mock_embedding_run_config):
    """Test that the engine returns an empty result for an empty input list without calling the client."""
    # 1. Setup
    engine = EmbeddingEngineService()
    # We don't even need to mock the client, as it should never be called.

    # 2. Execute
    result = await engine.run_batch(
        provider_config=mock_openai_embedding_provider_config,
        run_config=mock_embedding_run_config,
        texts=[]
    )

    # 3. Assert
    assert isinstance(result, BatchEmbeddingResult)
    assert result.results == []
    assert result.total_tokens == 0