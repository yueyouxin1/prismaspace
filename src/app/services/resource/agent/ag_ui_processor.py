from dataclasses import dataclass
from typing import Dict, List

from app.engine.model.llm import LLMMessage, LLMTool
from app.schemas.protocol import JsonValue, RunAgentInputExt
from app.services.resource.agent.ag_ui_normalizer import AgUiNormalizer
from app.services.exceptions import ServiceException


@dataclass
class AgUiProcessedRun:
    input_content: str | List[Dict[str, JsonValue]]
    history: List[LLMMessage]
    llm_tools: List[LLMTool]
    session_uuid: str


class AgUiProcessor:
    def __init__(self, normalizer: AgUiNormalizer):
        self.normalizer = normalizer

    def agui_to_agent_runtime(self, run_input: RunAgentInputExt) -> AgUiProcessedRun:
        if not isinstance(run_input.thread_id, str) or not run_input.thread_id.strip():
            raise ServiceException("threadId is required.")

        input_query, input_content_parts, history = self.normalizer.agui_messages_to_query_and_history(
            run_input.messages or []
        )

        resume_tool_messages = self.normalizer.agui_to_resume_tool_messages(run_input)
        if resume_tool_messages:
            history.extend(resume_tool_messages)
        # AG-UI interrupt resume can continue without a fresh user message.
        if not input_query and not input_content_parts and not resume_tool_messages:
            raise ServiceException("Run input messages must include at least one user message content.")

        input_content: str | List[Dict[str, JsonValue]] = (
            input_content_parts if input_content_parts else input_query
        )

        llm_tools: List[LLMTool] = []
        for tool_item in run_input.tools or []:
            tool = self.normalizer.agui_tool_to_llm_tool(tool_item)
            if tool:
                llm_tools.append(tool)

        return AgUiProcessedRun(
            input_content=input_content,
            history=history,
            llm_tools=llm_tools,
            session_uuid=run_input.thread_id,
        )
