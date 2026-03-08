from typing import Any, Dict, List, Optional

from app.models.resource.agent.session import AgentMessage


def extract_text_from_content_parts(content_parts: Optional[List[Dict[str, Any]]]) -> str:
    if not content_parts:
        return ""
    parts: List[str] = []
    for item in content_parts:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts).strip()


def normalize_text_content(
    text_content: Optional[str],
    content_parts: Optional[List[Dict[str, Any]]],
    legacy_content: Optional[str] = None,
) -> str:
    values = [value for value in [text_content, legacy_content] if isinstance(value, str) and value.strip()]
    if values:
        return "\n".join(values).strip()
    return extract_text_from_content_parts(content_parts)


def agent_message_to_text(message: AgentMessage) -> str:
    return normalize_text_content(
        text_content=getattr(message, "text_content", None),
        content_parts=getattr(message, "content_parts", None),
        legacy_content=getattr(message, "content", None),
    )


def llm_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return extract_text_from_content_parts(content)
    return ""
