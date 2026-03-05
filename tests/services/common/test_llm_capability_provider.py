from types import SimpleNamespace

import pytest

from app.services.common.llm_capability_provider import AICapabilityProvider
from app.services.exceptions import ConfigurationError


def test_resolve_llm_client_name_success():
    module_context = SimpleNamespace(
        version=SimpleNamespace(
            name="gpt-4o-2024-05-13",
            attributes={"client_name": "openai"},
        )
    )

    assert AICapabilityProvider._resolve_llm_client_name(module_context) == "openai"


def test_resolve_llm_client_name_requires_standard_field():
    module_context = SimpleNamespace(
        version=SimpleNamespace(
            name="legacy-model",
            attributes={"provider": "openai"},
        )
    )

    with pytest.raises(ConfigurationError, match="missing required 'client_name'"):
        AICapabilityProvider._resolve_llm_client_name(module_context)
