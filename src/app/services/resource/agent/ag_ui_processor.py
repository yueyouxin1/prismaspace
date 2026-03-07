from dataclasses import dataclass
from typing import Dict, List

from app.engine.model.llm import LLMMessage, LLMTool
from app.schemas.protocol import JsonValue, RunAgentInputExt
from app.services.resource.agent.ag_ui_normalizer import AgUiNormalizer
from app.services.resource.agent.protocol_adapter import AgUiProtocolAdapter


@dataclass
class AgUiProcessedRun:
    input_content: str | List[Dict[str, JsonValue]]
    custom_history: List[LLMMessage]
    resume_messages: List[LLMMessage]
    llm_tools: List[LLMTool]
    thread_id: str


class AgUiProcessor:
    def __init__(self, normalizer: AgUiNormalizer):
        self.adapter = AgUiProtocolAdapter(normalizer=normalizer)

    def agui_to_agent_runtime(self, run_input: RunAgentInputExt) -> AgUiProcessedRun:
        adapted = self.adapter.adapt(run_input)
        return AgUiProcessedRun(
            input_content=adapted.input_content,
            custom_history=adapted.custom_history,
            resume_messages=adapted.resume_messages,
            llm_tools=adapted.client_tools,
            thread_id=adapted.thread_id,
        )
