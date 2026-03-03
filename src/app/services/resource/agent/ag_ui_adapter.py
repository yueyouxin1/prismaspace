import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.engine.model.llm import LLMMessage, LLMTool, LLMToolCall, LLMToolFunction
from app.models import User
from app.schemas.protocol.ag_ui import AgUiMessage, AgUiRunAgentInput
from app.schemas.resource.agent.agent_schemas import AgentExecutionInputs, AgentExecutionRequest
from app.services.resource.agent.agent_service import AgentService


def encode_sse_data(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@dataclass
class AgUiExecutionContext:
    thread_id: str
    run_id: str
    parent_run_id: Optional[str]
    state: Any
    input_payload: Dict[str, Any]


class AgUiAgentAdapter:
    def __init__(self, service: AgentService):
        self.service = service

    def to_execution_request(self, run_input: AgUiRunAgentInput) -> tuple[AgentExecutionRequest, AgUiExecutionContext]:
        query, history = self._to_query_and_history(run_input.messages)

        # Inject context blocks as additional system history.
        for item in run_input.context:
            history.insert(
                0,
                LLMMessage(
                    role="system",
                    content=f"[AG-UI-CONTEXT]\nDescription: {item.description}\nValue:\n{item.value}",
                ),
            )

        # Resume payload can carry tool results after an interrupt.
        resume_tool_messages = self._parse_resume_tool_messages(run_input)
        if resume_tool_messages:
            history.extend(resume_tool_messages)
            if not query:
                query = "Continue based on provided tool results."

        # If no user message exists, keep a deterministic continuation query.
        if not query:
            query = "Continue."

        meta = {
            "ag_ui": {
                "thread_id": run_input.thread_id,
                "run_id": run_input.run_id,
                "parent_run_id": run_input.parent_run_id,
                "tools": [self._tool_to_llm_dict(tool) for tool in run_input.tools],
                "forwarded_props": run_input.forwarded_props,
                "resume": run_input.resume.model_dump(mode="json", by_alias=True) if run_input.resume else None,
            }
        }

        request = AgentExecutionRequest(
            meta=meta,
            inputs=AgentExecutionInputs(
                input_query=query,
                history=history,
                session_uuid=None,
            ),
        )

        context = AgUiExecutionContext(
            thread_id=run_input.thread_id,
            run_id=run_input.run_id,
            parent_run_id=run_input.parent_run_id,
            state=run_input.state,
            input_payload=run_input.model_dump(mode="json", by_alias=True),
        )
        return request, context

    async def stream_events(
        self,
        instance_uuid: str,
        run_input: AgUiRunAgentInput,
        actor: User,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        request, ag_ctx = self.to_execution_request(run_input)
        assistant_message_id = f"assistant-{ag_ctx.run_id}"
        reasoning_message_id = f"reasoning-{ag_ctx.run_id}"

        text_started = False
        reasoning_started = False
        done_sent = False

        yield {
            "type": "RUN_STARTED",
            "threadId": ag_ctx.thread_id,
            "runId": ag_ctx.run_id,
            "parentRunId": ag_ctx.parent_run_id,
            "input": ag_ctx.input_payload,
        }
        yield {
            "type": "MESSAGES_SNAPSHOT",
            "messages": run_input.model_dump(mode="json", by_alias=True).get("messages", []),
        }
        yield {"type": "STATE_SNAPSHOT", "snapshot": ag_ctx.state}
        yield {
            "type": "STATE_DELTA",
            "delta": [{"op": "add", "path": "/runStatus", "value": "running"}],
        }

        result = await self.service.async_execute(instance_uuid, request, actor)
        async for event in result.generator:
            name = event.event
            payload = event.data or {}

            if name == "message.delta":
                delta = str(payload.get("delta", ""))
                if not delta:
                    continue
                if not text_started:
                    text_started = True
                    yield {
                        "type": "TEXT_MESSAGE_START",
                        "messageId": assistant_message_id,
                        "role": "assistant",
                    }
                if delta:
                    yield {
                        "type": "TEXT_MESSAGE_CONTENT",
                        "messageId": assistant_message_id,
                        "delta": delta,
                    }
                continue

            if name == "reasoning.delta":
                delta = str(payload.get("delta", ""))
                if not reasoning_started:
                    reasoning_started = True
                    yield {"type": "REASONING_START", "messageId": reasoning_message_id}
                    yield {
                        "type": "REASONING_MESSAGE_START",
                        "messageId": reasoning_message_id,
                        "role": "assistant",
                    }
                if delta:
                    yield {
                        "type": "REASONING_MESSAGE_CONTENT",
                        "messageId": reasoning_message_id,
                        "delta": delta,
                    }
                continue

            if name == "tool.started":
                tool_calls = payload.get("tool_calls", []) or []
                for tool_call in tool_calls:
                    tool_call_id = tool_call.get("id")
                    function = tool_call.get("function", {}) or {}
                    tool_name = function.get("name", "")
                    tool_args = function.get("arguments", "{}")
                    if isinstance(tool_args, dict):
                        tool_args = json.dumps(tool_args, ensure_ascii=False)

                    yield {
                        "type": "TOOL_CALL_START",
                        "toolCallId": tool_call_id,
                        "toolCallName": tool_name,
                        "parentMessageId": assistant_message_id,
                    }
                    yield {
                        "type": "TOOL_CALL_ARGS",
                        "toolCallId": tool_call_id,
                        "delta": tool_args,
                    }
                    yield {"type": "TOOL_CALL_END", "toolCallId": tool_call_id}
                continue

            if name == "tool.finished":
                tool_call_id = payload.get("tool_call_id")
                output = payload.get("output")
                yield {
                    "type": "TOOL_CALL_RESULT",
                    "messageId": assistant_message_id,
                    "toolCallId": tool_call_id,
                    "content": json.dumps(output, ensure_ascii=False),
                    "role": "tool",
                }
                continue

            if name == "usage":
                yield {"type": "CUSTOM", "name": "usage", "value": payload}
                continue

            if name == "done":
                done_sent = True
                status = payload.get("status", "completed")
                if text_started:
                    yield {"type": "TEXT_MESSAGE_END", "messageId": assistant_message_id}
                if reasoning_started:
                    yield {"type": "REASONING_MESSAGE_END", "messageId": reasoning_message_id}
                    yield {"type": "REASONING_END", "messageId": reasoning_message_id}

                if status == "interrupt":
                    yield {
                        "type": "RUN_FINISHED",
                        "threadId": ag_ctx.thread_id,
                        "runId": ag_ctx.run_id,
                        "outcome": "interrupt",
                        "interrupt": payload.get("interrupt"),
                        "result": payload.get("result"),
                    }
                    yield {
                        "type": "STATE_DELTA",
                        "delta": [{"op": "replace", "path": "/runStatus", "value": "interrupted"}],
                    }
                elif status == "cancelled":
                    yield {
                        "type": "RUN_FINISHED",
                        "threadId": ag_ctx.thread_id,
                        "runId": ag_ctx.run_id,
                        "outcome": "interrupt",
                        "interrupt": {"reason": "cancelled"},
                        "result": payload.get("result"),
                    }
                    yield {
                        "type": "STATE_DELTA",
                        "delta": [{"op": "replace", "path": "/runStatus", "value": "cancelled"}],
                    }
                else:
                    yield {
                        "type": "RUN_FINISHED",
                        "threadId": ag_ctx.thread_id,
                        "runId": ag_ctx.run_id,
                        "outcome": "success",
                        "result": payload.get("result"),
                    }
                    yield {
                        "type": "STATE_DELTA",
                        "delta": [{"op": "replace", "path": "/runStatus", "value": "completed"}],
                    }
                break

            if name == "error":
                done_sent = True
                if text_started:
                    yield {"type": "TEXT_MESSAGE_END", "messageId": assistant_message_id}
                if reasoning_started:
                    yield {"type": "REASONING_MESSAGE_END", "messageId": reasoning_message_id}
                    yield {"type": "REASONING_END", "messageId": reasoning_message_id}
                yield {
                    "type": "RUN_ERROR",
                    "threadId": ag_ctx.thread_id,
                    "runId": ag_ctx.run_id,
                    "code": payload.get("code", "AGENT_RUNTIME_ERROR"),
                    "message": payload.get("message", "Unknown error"),
                    "retriable": payload.get("retriable", False),
                }
                yield {
                    "type": "STATE_DELTA",
                    "delta": [{"op": "replace", "path": "/runStatus", "value": "error"}],
                }
                break

        if not done_sent:
            if text_started:
                yield {"type": "TEXT_MESSAGE_END", "messageId": assistant_message_id}
            if reasoning_started:
                yield {"type": "REASONING_MESSAGE_END", "messageId": reasoning_message_id}
                yield {"type": "REASONING_END", "messageId": reasoning_message_id}
            yield {
                "type": "RUN_FINISHED",
                "threadId": ag_ctx.thread_id,
                "runId": ag_ctx.run_id,
                "outcome": "success",
            }
            yield {
                "type": "STATE_DELTA",
                "delta": [{"op": "replace", "path": "/runStatus", "value": "completed"}],
            }

    def _parse_resume_tool_messages(self, run_input: AgUiRunAgentInput) -> List[LLMMessage]:
        resume = run_input.resume
        if not resume or not isinstance(resume.payload, dict):
            return []

        payload = resume.payload
        tool_results = payload.get("tool_results") or payload.get("toolResults") or []
        parsed: List[LLMMessage] = []
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            tool_call_id = item.get("tool_call_id") or item.get("toolCallId") or item.get("id")
            if not tool_call_id:
                continue
            content_value = item.get("content")
            if content_value is None and "result" in item:
                content_value = item.get("result")
            content = content_value if isinstance(content_value, str) else json.dumps(content_value, ensure_ascii=False)
            parsed.append(
                LLMMessage(
                    role="tool",
                    tool_call_id=str(tool_call_id),
                    content=content,
                )
            )
        return parsed

    def _to_query_and_history(self, messages: List[AgUiMessage]) -> tuple[str, List[LLMMessage]]:
        if not messages:
            return "", []

        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user":
                last_user_idx = i
                break

        history: List[LLMMessage] = []
        query = ""
        for i, message in enumerate(messages):
            if i == last_user_idx:
                query = self._extract_user_text(message)
                continue
            mapped = self._to_llm_message(message)
            if mapped:
                history.append(mapped)

        return query, history

    def _to_llm_message(self, message: AgUiMessage) -> Optional[LLMMessage]:
        role = message.role
        if role == "user":
            return LLMMessage(role="user", content=self._extract_user_text(message))
        if role == "assistant":
            return LLMMessage(
                role="assistant",
                content=message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False),
                tool_calls=[tool_call.model_dump(mode="json", by_alias=True) for tool_call in (message.tool_calls or [])]
                if message.tool_calls
                else None,
            )
        if role == "tool":
            return LLMMessage(
                role="tool",
                content=message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False),
                tool_call_id=message.tool_call_id,
            )
        if role in ("developer", "system"):
            prefix = "[DEVELOPER]" if role == "developer" else "[SYSTEM]"
            content = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
            return LLMMessage(role="system", content=f"{prefix} {content}")

        # Activity / reasoning are retained as context hints.
        content = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
        return LLMMessage(role="system", content=f"[{role.upper()}] {content}")

    def _extract_user_text(self, message: AgUiMessage) -> str:
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
                elif isinstance(item, dict) and item.get("type") == "binary":
                    mime_type = item.get("mime_type") or item.get("mimeType") or "application/octet-stream"
                    text_parts.append(f"[binary:{mime_type}]")
            return "\n".join(part for part in text_parts if part)
        if content is None:
            return ""
        return json.dumps(content, ensure_ascii=False)

    def _tool_to_llm_dict(self, tool: Any) -> Dict[str, Any]:
        if isinstance(tool, dict):
            payload = tool
        else:
            payload = tool.model_dump(mode="json", by_alias=True)
        llm_tool = LLMTool(
            type="function",
            function=LLMToolFunction(
                name=payload["name"],
                description=payload.get("description", ""),
                parameters=payload.get("parameters", {"type": "object", "properties": {}}),
            ),
        )
        return llm_tool.model_dump(mode="json")
