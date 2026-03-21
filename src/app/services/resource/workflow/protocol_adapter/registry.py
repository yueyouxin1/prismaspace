from __future__ import annotations

from typing import Dict

from app.schemas.protocol import WORKFLOW_DEFAULT_PROTOCOL, WorkflowRuntimeProtocol
from .base import WorkflowProtocolAdapter
from .runtime import WorkflowRuntimeProtocolAdapter


class ProtocolAdapterRegistry:
    def __init__(self):
        self._adapters: Dict[str, WorkflowProtocolAdapter] = {}

    def register(self, protocol_name: WorkflowRuntimeProtocol, adapter: WorkflowProtocolAdapter) -> None:
        key = protocol_name.strip().lower()
        if not key:
            raise ValueError("protocol_name is required.")
        self._adapters[key] = adapter

    def get(self, protocol_name: WorkflowRuntimeProtocol | None) -> WorkflowProtocolAdapter | None:
        key = (protocol_name or "").strip().lower()
        if not key:
            return None
        return self._adapters.get(key)


def build_default_workflow_protocol_registry() -> ProtocolAdapterRegistry:
    registry = ProtocolAdapterRegistry()
    registry.register("wrp", WorkflowRuntimeProtocolAdapter())
    return registry


_registry = build_default_workflow_protocol_registry()


def get_workflow_protocol_adapter(protocol: WorkflowRuntimeProtocol | None = None) -> WorkflowProtocolAdapter:
    resolved = protocol or WORKFLOW_DEFAULT_PROTOCOL
    adapter = _registry.get(resolved)
    if adapter is None:
        raise NotImplementedError(f"Workflow protocol '{resolved}' is reserved but not implemented yet.")
    return adapter
