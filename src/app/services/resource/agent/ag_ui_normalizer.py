import json
from typing import Dict, List, Optional, Tuple

from app.engine.model.llm import LLMMessage, LLMTool, LLMToolFunction
from app.schemas.protocol import AgUiMessage, AgUiTool, JsonValue, RunAgentInputExt


class AgUiNormalizer:
    def agui_messages_to_query_and_history(
        self,
        messages: List[AgUiMessage],
    ) -> Tuple[str, Optional[List[Dict[str, JsonValue]]], List[LLMMessage]]:
        if not messages:
            return "", None, []

        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user":
                last_user_idx = i
                break

        query = ""
        query_parts: Optional[List[Dict[str, JsonValue]]] = None
        history: List[LLMMessage] = []

        for i, message in enumerate(messages):
            if i == last_user_idx:
                query, query_parts = self.agui_message_to_user_content(message)
                continue
            mapped = self.agui_message_to_llm_message(message)
            if mapped:
                history.append(mapped)

        return query, query_parts, history

    def agui_message_to_llm_message(self, message: AgUiMessage) -> Optional[LLMMessage]:
        role = message.role
        if role == "user":
            text, parts = self.agui_message_to_user_content(message)
            return LLMMessage(role="user", content=parts if parts else text)
        if role == "assistant":
            tool_calls = None
            if getattr(message, "tool_calls", None):
                tool_calls = [
                    tool_call.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for tool_call in message.tool_calls
                ]
            return LLMMessage(role="assistant", content=message.content, tool_calls=tool_calls)
        if role == "tool":
            return LLMMessage(
                role="tool",
                content=self._agui_to_content_text(message.content),
                tool_call_id=message.tool_call_id,
            )
        if role in ("developer", "system"):
            return LLMMessage(role="system", content=self._agui_to_content_text(message.content))
        return None

    def agui_message_to_user_content(
        self,
        message: AgUiMessage,
    ) -> Tuple[str, Optional[List[Dict[str, JsonValue]]]]:
        content = message.content
        if isinstance(content, str):
            return content, None
        if isinstance(content, list):
            text_parts: List[str] = []
            normalized_parts: List[Dict[str, JsonValue]] = []
            for item in content:
                if hasattr(item, "model_dump"):
                    item = item.model_dump(mode="json", by_alias=True, exclude_none=True)
                if isinstance(item, dict):
                    normalized_parts.append(item)
                    if item.get("type") == "text":
                        text_parts.append(str(item.get("text", "")))
                else:
                    normalized_parts.append({"type": "text", "text": str(item)})
                    text_parts.append(str(item))
            return " ".join(part for part in text_parts if part).strip(), normalized_parts
        if content is None:
            return "", None
        return self._agui_to_content_text(content), None

    def agui_to_resume_tool_messages(self, run_input: RunAgentInputExt) -> List[LLMMessage]:
        resume = run_input.resume
        if not resume:
            return []

        parsed: List[LLMMessage] = []
        for item in resume.payload.tool_results:
            content = self._agui_to_content_text(item.content)
            parsed.append(LLMMessage(role="tool", tool_call_id=item.tool_call_id, content=content))
        return parsed

    def agui_tool_to_llm_tool(self, tool: AgUiTool) -> Optional[LLMTool]:
        tool_name = tool.name
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        return LLMTool(
            type="function",
            function=LLMToolFunction(
                name=tool_name.strip(),
                description=tool.description or "",
                parameters=tool.parameters or {"type": "object", "properties": {}},
            ),
        )

    @staticmethod
    def _agui_to_content_text(content: JsonValue) -> str:
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        return json.dumps(content, ensure_ascii=False)
