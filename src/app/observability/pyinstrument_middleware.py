import logging
from pathlib import Path
from urllib.parse import parse_qs

from pyinstrument import Profiler
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings


profile_logger = logging.getLogger("app.pyinstrument")
profile_logger.setLevel(logging.INFO)
if not profile_logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    profile_logger.addHandler(handler)
profile_logger.propagate = False


class PyInstrumentProfilingMiddleware:
    """
    按请求触发的轻量诊断 profiler。

    默认关闭，只有启用并通过 query/header 显式触发时才会生成 profile。
    """

    def __init__(self, app: ASGIApp):
        self.app = app
        self.output_dir = Path(settings.PYINSTRUMENT_PROFILE_DIR)
        self.interval = float(settings.PYINSTRUMENT_INTERVAL_SECONDS)
        self.query_key = settings.PYINSTRUMENT_TRIGGER_QUERY
        self.header_name = settings.PYINSTRUMENT_TRIGGER_HEADER.lower().encode("ascii")

    def _is_triggered(self, scope: Scope) -> bool:
        query_string = scope.get("query_string", b"").decode("utf-8", errors="ignore")
        if self.query_key and parse_qs(query_string).get(self.query_key):
            return True

        headers = scope.get("headers", [])
        for name, value in headers:
            if name.lower() == self.header_name and value.decode("utf-8", errors="ignore").lower() in {"1", "true", "yes"}:
                return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_triggered(scope):
            await self.app(scope, receive, send)
            return

        profiler = Profiler(interval=self.interval, async_mode="enabled")
        profiler.start()
        profile_path = self._build_profile_path(scope)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-pyinstrument-profile", str(profile_path).encode("utf-8")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            profiler.stop()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(profiler.output_html(), encoding="utf-8")
            profile_logger.info("pyinstrument profile written: %s", profile_path)

    def _build_profile_path(self, scope: Scope) -> Path:
        method = scope.get("method", "HTTP").lower()
        raw_path = scope.get("path", "/").strip("/") or "root"
        safe_path = raw_path.replace("/", "_").replace("\\", "_")
        filename = f"{method}_{safe_path}.html"
        return self.output_dir / filename
