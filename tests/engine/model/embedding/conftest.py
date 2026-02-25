# tests/engine/model/embedding/conftest.py
import pytest
from app.engine.model.embedding import (
    EmbeddingProviderConfig,
    EmbeddingRunConfig
)

@pytest.fixture
def mock_openai_embedding_provider_config():
    """A standard OpenAI provider config for embeddings."""
    return EmbeddingProviderConfig(
        client_name="openai",
        api_key="fake-embedding-key",
        base_url="http://localhost:8080/v1"
    )

@pytest.fixture
def mock_embedding_run_config():
    """A standard embedding run config."""
    return EmbeddingRunConfig(model="text-embedding-3-small")

@pytest.fixture
def mock_texts_input():
    """A sample list of texts to be embedded."""
    return ["hello world", "pytest is awesome"]