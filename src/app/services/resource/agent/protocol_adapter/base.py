from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.engine.model.llm import LLMMessage, LLMTool
from app.schemas.protocol import JsonValue


@runtime_checkable
class ClientToolRegistrar(Protocol):
    def register_client_tool(self, tool_def: LLMTool) -> None:
        ...


@dataclass(frozen=True)
class ProtocolAdaptedRun:
    input_content: str | List[Dict[str, JsonValue]]
    thread_id: str
    client_tools: List[LLMTool]
    custom_history: List[LLMMessage] = field(default_factory=list)
    resume_messages: List[LLMMessage] = field(default_factory=list)
    has_custom_history: bool = False
    resume_tool_call_ids: List[str] = field(default_factory=list)
    resume_interrupt_id: Optional[str] = None


class ProtocolAdapter(ABC):
    @abstractmethod
    def adapt(
        self,
        run_input: Any,
        *,
        tool_registrar: Optional[ClientToolRegistrar] = None,
    ) -> ProtocolAdaptedRun:
        ...
