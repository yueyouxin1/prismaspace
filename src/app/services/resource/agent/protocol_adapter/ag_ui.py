from __future__ import annotations

import logging
from typing import List, Optional

from app.engine.model.llm import LLMTool
from app.schemas.protocol import JsonValue, RunAgentInputExt
from app.services.exceptions import ServiceException
from app.services.resource.agent.ag_ui_normalizer import AgUiNormalizer
from app.services.resource.agent.protocol_adapter.base import (
    ClientToolRegistrar,
    ProtocolAdaptedRun,
    ProtocolAdapter,
)

logger = logging.getLogger(__name__)


class AgUiProtocolAdapter(ProtocolAdapter):
    def __init__(self, normalizer: Optional[AgUiNormalizer] = None):
        self.normalizer = normalizer or AgUiNormalizer()

    def adapt(
        self,
        run_input: RunAgentInputExt,
        *,
        tool_registrar: Optional[ClientToolRegistrar] = None,
    ) -> ProtocolAdaptedRun:
        thread_id = run_input.thread_id.strip() if isinstance(run_input.thread_id, str) else ""

        context_messages = self.normalizer.agui_context_to_llm_messages(run_input.context or [])
        input_query, input_content_parts, custom_history = self.normalizer.agui_messages_to_query_and_history(
            run_input.messages or []
        )
        has_custom_history = bool(custom_history)
        if context_messages:
            custom_history = [*context_messages, *custom_history]

        resume_tool_messages = self.normalizer.agui_to_resume_tool_messages(run_input)
        resume_tool_call_ids: List[str] = []
        if resume_tool_messages:
            for tool_message in resume_tool_messages:
                if tool_message.tool_call_id:
                    resume_tool_call_ids.append(tool_message.tool_call_id)

        if (
            not input_query
            and not input_content_parts
            and not resume_tool_messages
        ):
            raise ServiceException("Run input messages must include at least one user message content.")

        client_tools: List[LLMTool] = []
        for tool_item in run_input.tools or []:
            tool = self.normalizer.agui_tool_to_llm_tool(tool_item)
            if tool:
                client_tools.append(tool)
                if tool_registrar:
                    self._safe_register_client_tool(tool_registrar=tool_registrar, tool=tool)

        input_content: str | List[Dict[str, JsonValue]] = (
            input_content_parts if input_content_parts else input_query
        )

        return ProtocolAdaptedRun(
            input_content=input_content,
            thread_id=thread_id,
            client_tools=client_tools,
            custom_history=custom_history,
            resume_messages=resume_tool_messages,
            has_custom_history=has_custom_history,
            resume_tool_call_ids=resume_tool_call_ids,
            resume_interrupt_id=run_input.resume.interrupt_id if run_input.resume else None,
        )

    @staticmethod
    def _safe_register_client_tool(tool_registrar: ClientToolRegistrar, tool: LLMTool) -> None:
        try:
            tool_registrar.register_client_tool(tool)
        except Exception as exc:
            logger.warning("Invalid AG-UI tool definition ignored: %s", exc)
