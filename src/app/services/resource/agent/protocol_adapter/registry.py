from __future__ import annotations

from typing import Dict

from app.services.resource.agent.protocol_adapter.base import ProtocolAdapter


class ProtocolAdapterRegistry:
    def __init__(self):
        self._adapters: Dict[str, ProtocolAdapter] = {}

    def register(self, protocol_name: str, adapter: ProtocolAdapter) -> None:
        key = protocol_name.strip().lower()
        if not key:
            raise ValueError("protocol_name is required.")
        self._adapters[key] = adapter

    def get(self, protocol_name: str) -> ProtocolAdapter | None:
        key = protocol_name.strip().lower()
        if not key:
            return None
        return self._adapters.get(key)
