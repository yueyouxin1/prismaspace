import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect
from ag_ui.core import EventType, RunErrorEvent

from app.api.dependencies.ws_auth import AuthContext
from app.core.context import AppContext
from app.db.session import SessionLocal
from app.schemas.protocol import RunAgentInputExt
from app.services.resource.agent.agent_service import AgentService

logger = logging.getLogger(__name__)


class AgentSessionHandler:
    def __init__(self, websocket: WebSocket, auth_context: AuthContext):
        self.websocket = websocket
        self.user = auth_context.user
        self.auth_context = auth_context
        self.current_task: Optional[asyncio.Task] = None
        self.current_run_id: Optional[str] = None

    async def run(self):
        await self.websocket.accept()
        cancel_on_close = False
        try:
            while True:
                text = await self.websocket.receive_text()
                await self._dispatch(text)
        except WebSocketDisconnect:
            logger.info("AG-UI websocket disconnected: %s", self.user.uuid)
            cancel_on_close = False
        finally:
            if cancel_on_close:
                await self._cancel_current_task()

    async def _dispatch(self, text: str):
        try:
            payload = json.loads(text)
        except Exception:
            await self._send_run_error(
                run_id="unknown",
                thread_id="unknown",
                code="AG_UI_PROTOCOL_ERROR",
                message="Invalid JSON payload",
            )
            return

        # Runtime control channel via AG-UI custom event.
        if (
            isinstance(payload, dict)
            and payload.get("type") == "CUSTOM"
            and payload.get("name") == "ps.cancel_run"
        ):
            target_run_id = None
            value = payload.get("value")
            if isinstance(value, dict) and isinstance(value.get("runId"), str):
                target_run_id = value.get("runId")
            if target_run_id and self.current_run_id and target_run_id != self.current_run_id:
                await self._send_json(
                    {
                        "type": "CUSTOM",
                        "name": "ps.control.cancel_ignored",
                        "value": {"currentRunId": self.current_run_id, "targetRunId": target_run_id},
                    }
                )
                return
            await self._cancel_current_task()
            await self._send_json(
                {
                    "type": "CUSTOM",
                    "name": "ps.control.cancelled",
                    "value": {"status": "ok"},
                }
            )
            return

        try:
            run_input = RunAgentInputExt.model_validate(payload)
        except Exception as exc:
            run_id = payload.get("runId") if isinstance(payload, dict) else "unknown"
            thread_id = payload.get("threadId") if isinstance(payload, dict) else "unknown"
            await self._send_run_error(
                run_id=str(run_id or "unknown"),
                thread_id=str(thread_id or "unknown"),
                code="AG_UI_VALIDATION_ERROR",
                message=f"Invalid RunAgentInput: {exc}",
            )
            return

        agent_uuid = self._extract_agent_uuid(run_input)
        if not agent_uuid:
            await self._send_run_error(
                run_id=run_input.run_id,
                thread_id=run_input.thread_id,
                code="AG_UI_MISSING_AGENT_UUID",
                message="Missing websocket-only agent uuid in forwardedProps.platform.agentUuid",
            )
            return

        await self._cancel_current_task()
        self.current_run_id = None
        self.current_task = asyncio.create_task(self._run_chat_stream(agent_uuid, run_input))
        self.current_task.add_done_callback(self._cleanup_task)

    def _cleanup_task(self, task: asyncio.Task):
        if self.current_task is task:
            self.current_task = None
            self.current_run_id = None

    async def _cancel_current_task(self):
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("WebSocket run cancellation failed: %s", exc, exc_info=True)
            finally:
                self.current_task = None

    async def _run_chat_stream(self, agent_uuid: str, run_input: RunAgentInputExt):
        run_result = None
        cancel_fn = None
        async with SessionLocal() as db:
            app_context = AppContext(
                db=db,
                db_session_factory=SessionLocal,
                auth=self.auth_context,
                redis_service=self.websocket.app.state.redis_service,
                vector_manager=self.websocket.app.state.vector_manager,
                arq_pool=self.websocket.app.state.arq_pool,
            )
            service = AgentService(app_context)
            try:
                active_run = await service.get_active_run(agent_uuid, self.user, run_input.thread_id)
                if active_run:
                    active_run_id = active_run["run_id"] if isinstance(active_run, dict) else getattr(active_run, "run_id", None)
                    self.current_run_id = active_run_id
                    async for envelope in service.stream_live_run_events(active_run_id, after_seq=0):
                        await self._send_event(envelope.get("payload", envelope))
                else:
                    run_result = await service.async_execute(agent_uuid, run_input, self.user)
                    cancel_fn = getattr(run_result, "cancel", None)
                    self.current_run_id = getattr(run_result, "run_id", None)
                    async for event in run_result.generator:
                        await self._send_event(event)
            except asyncio.CancelledError:
                if callable(cancel_fn):
                    cancel_fn()
                await self._send_json(
                    {
                        "type": "CUSTOM",
                        "name": "ps.control.cancelled",
                        "value": {
                            "runId": getattr(run_result, "run_id", run_input.run_id) if run_result else run_input.run_id,
                            "threadId": getattr(run_result, "thread_id", run_input.thread_id) if run_result else run_input.thread_id,
                        },
                    }
                )
                raise
            except Exception as exc:
                logger.error("AG-UI websocket stream failed: %s", exc, exc_info=True)
                await self._send_run_error(
                    run_id=getattr(run_result, "run_id", run_input.run_id) if run_result else run_input.run_id,
                    thread_id=getattr(run_result, "thread_id", run_input.thread_id) if run_result else run_input.thread_id,
                    code="AGENT_RUNTIME_ERROR",
                    message=str(exc),
                )
            finally:
                if callable(cancel_fn):
                    cancel_fn()
                if run_result and getattr(run_result, "task", None):
                    try:
                        await run_result.task
                    except Exception:
                        pass

    async def _send_event(self, event: Any):
        if hasattr(event, "model_dump_json"):
            payload = event.model_dump_json(by_alias=True, exclude_none=True)
            await self.websocket.send_text(payload)
            return
        if isinstance(event, dict):
            await self._send_json(event)
            return
        await self._send_json({"type": "RAW", "event": str(event), "source": "prismaspace.agent.websocket"})

    async def _send_json(self, payload: dict):
        try:
            await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))
        except RuntimeError:
            pass

    async def _send_run_error(self, run_id: str, thread_id: str, code: str, message: str):
        await self._send_event(
            RunErrorEvent(
                type=EventType.RUN_ERROR,
                threadId=thread_id,
                runId=run_id,
                code=code,
                message=message,
                retriable=False,
            )
        )

    @staticmethod
    def _extract_agent_uuid(run_input: RunAgentInputExt) -> Optional[str]:
        platform = run_input.platform_props
        if not platform:
            return None
        return platform.agent_uuid
