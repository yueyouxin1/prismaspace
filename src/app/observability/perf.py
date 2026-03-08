import json
import logging
import random
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sqlalchemy import event
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings


perf_logger = logging.getLogger("app.perf")
perf_logger.setLevel(logging.INFO)
if not perf_logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    perf_logger.addHandler(handler)
perf_logger.propagate = False

_perf_state_ctx: ContextVar[Optional["RequestPerfState"]] = ContextVar("request_perf_state", default=None)
_OBSERVED_PATH_PREFIXES = ("/api/v1/agent/", "/api/v1/workflow/", "/api/v1/execute/")
_REASONING_EVENT_TYPES = {"REASONING_MESSAGE_CONTENT"}
_TEXT_EVENT_TYPES = {"TEXT_MESSAGE_CONTENT"}


@dataclass
class RequestPerfState:
    request_id: str
    method: str
    path: str
    started_ns: int
    sampled: bool
    status_code: Optional[int] = None
    response_started_ns: Optional[int] = None
    first_body_ns: Optional[int] = None
    completed_ns: Optional[int] = None
    first_agent_event_ns: Optional[int] = None
    first_agent_event_type: Optional[str] = None
    first_reasoning_token_ns: Optional[int] = None
    first_text_token_ns: Optional[int] = None
    db_query_count: int = 0
    db_query_total_ns: int = 0
    db_query_count_at_first_reasoning: Optional[int] = None
    db_query_total_ns_at_first_reasoning: Optional[int] = None
    db_query_count_at_first_text: Optional[int] = None
    db_query_total_ns_at_first_text: Optional[int] = None
    slow_queries: list[Dict[str, Any]] = field(default_factory=list)

    @property
    def observed(self) -> bool:
        return self.sampled and self.path.startswith(_OBSERVED_PATH_PREFIXES)

    def mark_response_started(self, status_code: int) -> None:
        if self.response_started_ns is None:
            self.response_started_ns = time.perf_counter_ns()
            self.status_code = status_code

    def mark_first_body(self) -> None:
        if self.first_body_ns is None:
            self.first_body_ns = time.perf_counter_ns()

    def mark_agent_event(self, event_type: str) -> None:
        if not self.observed:
            return
        now_ns = time.perf_counter_ns()
        if self.first_agent_event_ns is None:
            self.first_agent_event_ns = now_ns
            self.first_agent_event_type = event_type
        if event_type in _REASONING_EVENT_TYPES and self.first_reasoning_token_ns is None:
            self.first_reasoning_token_ns = now_ns
            self.db_query_count_at_first_reasoning = self.db_query_count
            self.db_query_total_ns_at_first_reasoning = self.db_query_total_ns
        if event_type in _TEXT_EVENT_TYPES and self.first_text_token_ns is None:
            self.first_text_token_ns = now_ns
            self.db_query_count_at_first_text = self.db_query_count
            self.db_query_total_ns_at_first_text = self.db_query_total_ns

    def record_query(self, statement: str, duration_ns: int) -> None:
        self.db_query_count += 1
        self.db_query_total_ns += duration_ns
        duration_ms = round(duration_ns / 1_000_000, 3)
        if duration_ms < settings.PERF_OBSERVABILITY_SLOW_SQL_MS:
            return
        normalized = " ".join(statement.split())
        self.slow_queries.append(
            {
                "duration_ms": duration_ms,
                "sql": normalized[:240],
            }
        )
        if len(self.slow_queries) > 5:
            self.slow_queries.sort(key=lambda item: item["duration_ms"], reverse=True)
            del self.slow_queries[5:]

    def finalize(self) -> Dict[str, Any]:
        self.completed_ns = time.perf_counter_ns()
        summary: Dict[str, Any] = {
            "type": "http_perf",
            "request_id": self.request_id,
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "total_ms": _ns_to_ms(self.completed_ns - self.started_ns),
            "response_start_ms": _delta_ms(self.response_started_ns, self.started_ns),
            "first_body_ms": _delta_ms(self.first_body_ns, self.started_ns),
            "db_query_count": self.db_query_count,
            "db_query_ms": _ns_to_ms(self.db_query_total_ns),
            "slow_queries": self.slow_queries,
        }
        if self.observed:
            summary.update(
                {
                    "first_agent_event_type": self.first_agent_event_type,
                    "first_agent_event_ms": _delta_ms(self.first_agent_event_ns, self.started_ns),
                    "first_reasoning_token_ms": _delta_ms(self.first_reasoning_token_ns, self.started_ns),
                    "first_text_token_ms": _delta_ms(self.first_text_token_ns, self.started_ns),
                    "db_query_count_before_first_reasoning": self.db_query_count_at_first_reasoning,
                    "db_query_ms_before_first_reasoning": _optional_ns_to_ms(self.db_query_total_ns_at_first_reasoning),
                    "db_query_count_before_first_text": self.db_query_count_at_first_text,
                    "db_query_ms_before_first_text": _optional_ns_to_ms(self.db_query_total_ns_at_first_text),
                }
            )
        return summary


def _ns_to_ms(duration_ns: int) -> float:
    return round(duration_ns / 1_000_000, 3)


def _optional_ns_to_ms(duration_ns: Optional[int]) -> Optional[float]:
    if duration_ns is None:
        return None
    return _ns_to_ms(duration_ns)


def _delta_ms(mark_ns: Optional[int], base_ns: int) -> Optional[float]:
    if mark_ns is None:
        return None
    return _ns_to_ms(mark_ns - base_ns)


def _is_enabled() -> bool:
    return bool(settings.PERF_OBSERVABILITY_ENABLED)


def _should_sample(path: str) -> bool:
    if not _is_enabled():
        return False
    sample_rate = min(max(float(settings.PERF_OBSERVABILITY_SAMPLE_RATE), 0.0), 1.0)
    if sample_rate >= 1.0:
        return True
    return random.random() <= sample_rate


def begin_request(scope: Scope) -> Optional[Token]:
    if scope["type"] != "http":
        return None
    path = scope.get("path", "")
    state = RequestPerfState(
        request_id=uuid.uuid4().hex,
        method=scope.get("method", "UNKNOWN"),
        path=path,
        started_ns=time.perf_counter_ns(),
        sampled=_should_sample(path),
    )
    return _perf_state_ctx.set(state)


def get_request_perf_state() -> Optional[RequestPerfState]:
    return _perf_state_ctx.get()


def observe_agent_stream_event(event: Any) -> None:
    state = get_request_perf_state()
    if state is None or not state.sampled:
        return
    event_type = getattr(event, "type", None)
    if event_type is None and isinstance(event, dict):
        event_type = event.get("type")
    if isinstance(event_type, str):
        state.mark_agent_event(event_type)


def _reset_request(token: Optional[Token]) -> None:
    if token is None:
        return
    _perf_state_ctx.reset(token)


def _emit_summary(state: Optional[RequestPerfState]) -> None:
    if state is None or not state.sampled:
        return
    perf_logger.info(json.dumps(state.finalize(), ensure_ascii=False))


class PerformanceObservabilityMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = begin_request(scope)
        state = get_request_perf_state()

        async def send_wrapper(message: Message) -> None:
            if state and state.sampled:
                if message["type"] == "http.response.start":
                    state.mark_response_started(message["status"])
                    headers = list(message.get("headers", []))
                    headers.append((b"x-perf-request-id", state.request_id.encode("ascii")))
                    message["headers"] = headers
                elif message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    if body:
                        state.mark_first_body()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                _emit_summary(state)
            finally:
                _reset_request(token)


def install_sqlalchemy_observers(sync_engine) -> None:
    if getattr(sync_engine, "_perf_observers_installed", False):
        return
    sync_engine._perf_observers_installed = True

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("_perf_query_start_ns_stack", []).append(time.perf_counter_ns())

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        stack = conn.info.get("_perf_query_start_ns_stack") or []
        start_ns = stack.pop() if stack else None
        if start_ns is None:
            return
        state = get_request_perf_state()
        if state is None or not state.sampled:
            return
        state.record_query(statement, time.perf_counter_ns() - start_ns)

    @event.listens_for(sync_engine, "handle_error")
    def _handle_error(exception_context):
        conn = exception_context.connection
        if conn is None:
            return
        stack = conn.info.get("_perf_query_start_ns_stack") or []
        start_ns = stack.pop() if stack else None
        if start_ns is None:
            return
        state = get_request_perf_state()
        if state is None or not state.sampled:
            return
        state.record_query(exception_context.statement or "<error>", time.perf_counter_ns() - start_ns)
