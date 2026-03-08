import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.observability import perf
from app.observability.pyinstrument_middleware import PyInstrumentProfilingMiddleware


@pytest.mark.asyncio
async def test_observe_agent_stream_event_snapshots_db_state(monkeypatch):
    monkeypatch.setattr(perf.settings, "PERF_OBSERVABILITY_ENABLED", True)
    monkeypatch.setattr(perf.settings, "PERF_OBSERVABILITY_SAMPLE_RATE", 1.0)

    token = perf.begin_request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/agent/instance-1/sse",
        }
    )
    state = perf.get_request_perf_state()
    assert state is not None

    state.db_query_count = 3
    state.db_query_total_ns = 20_000_000
    perf.observe_agent_stream_event(SimpleNamespace(type="REASONING_MESSAGE_CONTENT"))

    state.db_query_count = 5
    state.db_query_total_ns = 45_000_000
    perf.observe_agent_stream_event(SimpleNamespace(type="TEXT_MESSAGE_CONTENT"))

    assert state.first_reasoning_token_ns is not None
    assert state.first_text_token_ns is not None
    assert state.db_query_count_at_first_reasoning == 3
    assert state.db_query_total_ns_at_first_reasoning == 20_000_000
    assert state.db_query_count_at_first_text == 5
    assert state.db_query_total_ns_at_first_text == 45_000_000

    perf._perf_state_ctx.reset(token)


@pytest.mark.asyncio
async def test_performance_middleware_adds_header_and_logs_summary(monkeypatch):
    monkeypatch.setattr(perf.settings, "PERF_OBSERVABILITY_ENABLED", True)
    monkeypatch.setattr(perf.settings, "PERF_OBSERVABILITY_SAMPLE_RATE", 1.0)

    logged = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent_messages = []

    async def send(message):
        sent_messages.append(message)

    monkeypatch.setattr(perf.perf_logger, "info", lambda payload: logged.append(payload))

    middleware = perf.PerformanceObservabilityMiddleware(app)
    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/agent/instance-1/sse",
            "headers": [],
        },
        receive,
        send,
    )

    response_start = sent_messages[0]
    assert response_start["type"] == "http.response.start"
    header_names = [name for name, _ in response_start["headers"]]
    assert b"x-perf-request-id" in header_names

    assert len(logged) == 1
    payload = json.loads(logged[0])
    assert payload["type"] == "http_perf"
    assert payload["path"] == "/api/v1/agent/instance-1/sse"
    assert payload["status_code"] == 200


def test_perf_logger_is_info_visible():
    assert perf.perf_logger.level <= 20
    assert perf.perf_logger.handlers


@pytest.mark.asyncio
async def test_pyinstrument_middleware_writes_profile_on_trigger(monkeypatch, tmp_path):
    class FakeProfiler:
        def __init__(self, interval, async_mode):
            self.interval = interval
            self.async_mode = async_mode
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def output_html(self):
            return "<html>profile</html>"

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    from app.observability import pyinstrument_middleware as pyinstrument_module

    monkeypatch.setattr(pyinstrument_module, "Profiler", FakeProfiler)
    monkeypatch.setattr(pyinstrument_module.settings, "PYINSTRUMENT_PROFILE_DIR", str(tmp_path))
    monkeypatch.setattr(pyinstrument_module.settings, "PYINSTRUMENT_INTERVAL_SECONDS", 0.002)
    monkeypatch.setattr(pyinstrument_module.settings, "PYINSTRUMENT_TRIGGER_QUERY", "profile")
    monkeypatch.setattr(pyinstrument_module.settings, "PYINSTRUMENT_TRIGGER_HEADER", "X-Profile")

    middleware = PyInstrumentProfilingMiddleware(app)
    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/agent/demo/sse",
            "query_string": b"profile=1",
            "headers": [],
        },
        receive,
        send,
    )

    response_start = sent[0]
    assert response_start["type"] == "http.response.start"
    header_map = dict(response_start["headers"])
    assert b"x-pyinstrument-profile" in header_map

    profile_path = Path(header_map[b"x-pyinstrument-profile"].decode("utf-8"))
    assert profile_path.exists()
    assert profile_path.read_text(encoding="utf-8") == "<html>profile</html>"
