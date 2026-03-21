# src/app/api/v1/workflow/ws_handler.py

import asyncio
import json
import logging
from typing import Optional

from pydantic import TypeAdapter

from app.api.websocket.base import BaseWebSocketHandler, WSPacket
from app.core.context import AppContext
from app.db.session import SessionLocal
from app.schemas.protocol import (
    WorkflowRuntimeControlMessage,
    WorkflowRuntimeRunAttachMessage,
    WorkflowRuntimeRunCancelMessage,
    WorkflowRuntimeRunResumeMessage,
    WorkflowRuntimeRunStartMessage,
    WorkflowRuntimeActiveRunResolveMessage,
    WorkflowRuntimeUiEventAbortMessage,
    WorkflowRuntimeUiEventSubmitMessage,
)
from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionRequest
from app.services.exceptions import ServiceException
from app.services.resource.workflow.protocol_bridge import WorkflowProtocolBridgeService
from app.services.resource.workflow.workflow_service import WorkflowService

logger = logging.getLogger(__name__)

_runtime_message_adapter = TypeAdapter(WorkflowRuntimeControlMessage)


class WorkflowSessionHandler(BaseWebSocketHandler):
    """
    Workflow WebSocket handler.
    优先支持 Workflow Runtime Protocol；同时兼容旧的 action=run/stop 包格式。
    """

    def __init__(self, websocket, auth_context):
        super().__init__(websocket, auth_context)
        self.current_task: Optional[asyncio.Task] = None
        self.current_run_id: Optional[str] = None
        self.current_detach = None
        self.current_trace_id: Optional[str] = None
        self.current_thread_id: Optional[str] = None
        self.current_parent_run_id: Optional[str] = None

    async def _dispatch(self, text: str):
        try:
            payload = json.loads(text)
        except Exception:
            await self.reply_error(None, "Protocol Error: Invalid JSON payload")
            return

        if isinstance(payload, dict) and isinstance(payload.get("type"), str):
            try:
                message = _runtime_message_adapter.validate_python(payload)
            except Exception as exc:
                await self._send_runtime_error(run_id="unknown", message=f"Invalid workflow runtime message: {exc}")
                return
            await self._dispatch_runtime_message(message)
            return

        await super()._dispatch(text)

    async def _dispatch_runtime_message(self, message):
        try:
            WorkflowProtocolBridgeService.resolve_adapter(getattr(message, "protocol", None))
        except ServiceException as exc:
            await self._send_runtime_error(run_id="unknown", message=str(exc))
            return

        if isinstance(message, WorkflowRuntimeRunStartMessage):
            await self._detach_current_stream()
            self.current_task = asyncio.create_task(
                self._run_workflow_stream(
                    instance_uuid=message.instance_uuid,
                    request=message.input,
                    request_id=message.request_id,
                )
            )
            self.current_task.add_done_callback(self._cleanup_task)
            return

        if isinstance(message, WorkflowRuntimeRunResumeMessage):
            await self._detach_current_stream()
            self.current_task = asyncio.create_task(
                self._run_workflow_stream(
                    instance_uuid=message.instance_uuid,
                    request=WorkflowExecutionRequest(
                        resume_from_run_id=message.run_id,
                        resume=message.resume,
                    ),
                    request_id=message.request_id,
                )
            )
            self.current_task.add_done_callback(self._cleanup_task)
            return

        if isinstance(message, WorkflowRuntimeRunAttachMessage):
            await self._detach_current_stream()
            self.current_run_id = message.run_id
            self.current_task = asyncio.create_task(
                self._attach_live_run(message.run_id, after_seq=message.after_seq, protocol=message.protocol)
            )
            self.current_task.add_done_callback(self._cleanup_task)
            return

        if isinstance(message, WorkflowRuntimeRunCancelMessage):
            target_run_id = message.run_id or self.current_run_id
            if not target_run_id:
                await self._send_runtime_error(run_id="unknown", message="run.cancel requires runId")
                return
            await self._request_run_cancel(target_run_id)
            return

        if isinstance(message, WorkflowRuntimeUiEventSubmitMessage):
            await self._send_runtime_error(
                run_id=message.run_id,
                message="ui.event.submit is not enabled on the workflow runtime surface yet.",
            )
            return

        if isinstance(message, WorkflowRuntimeUiEventAbortMessage):
            await self._send_runtime_error(
                run_id=message.run_id,
                message="ui.event.abort is not enabled on the workflow runtime surface yet.",
            )
            return

        if isinstance(message, WorkflowRuntimeActiveRunResolveMessage):
            await self._send_runtime_error(
                run_id="unknown",
                message="active-run.resolve is reserved for scoped interactive workflow profiles only.",
            )
            return

        await self._send_runtime_error(run_id="unknown", message="Unsupported workflow runtime message")

    async def action_run(self, packet: WSPacket):
        await self._detach_current_stream()

        instance_uuid = packet.data.get("instance_uuid")
        if not instance_uuid:
            await self.reply_error(packet.request_id, "Missing instance_uuid")
            return

        try:
            inputs_data = packet.data.get("inputs", {})
            request = WorkflowExecutionRequest(inputs=inputs_data)
        except Exception as exc:
            await self.reply_error(packet.request_id, f"Invalid Params: {exc}")
            return

        self.current_task = asyncio.create_task(
            self._run_workflow_stream(
                instance_uuid=str(instance_uuid),
                request=request,
                request_id=packet.request_id,
            )
        )
        self.current_task.add_done_callback(self._cleanup_task)

    async def action_stop(self, packet: WSPacket):
        if self.current_run_id:
            logger.info("User requested stop for workflow run %s", self.current_run_id)
            await self._request_run_cancel(self.current_run_id)
            await self.send("stopping", {"message": "Workflow cancellation requested.", "run_id": self.current_run_id}, packet.request_id)

    def _cleanup_task(self, task: asyncio.Task):
        if self.current_task == task:
            self.current_task = None
            self.current_run_id = None
            self.current_detach = None
            self.current_trace_id = None
            self.current_thread_id = None
            self.current_parent_run_id = None

    async def on_disconnect(self):
        if callable(self.current_detach):
            self.current_detach()

    async def _detach_current_stream(self):
        if callable(self.current_detach):
            self.current_detach()
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("Error during workflow task cancellation: %s", exc, exc_info=True)
            finally:
                self._cleanup_task(self.current_task)

    async def _run_workflow_stream(self, *, instance_uuid: str, request: WorkflowExecutionRequest, request_id: Optional[str]):
        async with SessionLocal() as db:
            try:
                app_context = AppContext(
                    db=db,
                    auth=self.auth_context,
                    redis_service=self.websocket.app.state.redis_service,
                    vector_manager=self.websocket.app.state.vector_manager,
                    arq_pool=self.websocket.app.state.arq_pool,
                )
                service = WorkflowService(app_context)
                bridge = WorkflowProtocolBridgeService(service)
                stream = await bridge.execute_stream(
                    instance_uuid=instance_uuid,
                    request=request,
                    actor=self.user,
                )
                self.current_run_id = stream.run_id
                self.current_thread_id = stream.thread_id
                self.current_trace_id = stream.trace_id
                self.current_parent_run_id = stream.parent_run_id
                self.current_detach = stream.detach
                async for envelope in stream.generator:
                    await self._send_runtime_envelope(envelope)
            except asyncio.CancelledError:
                raise
            except ServiceException as exc:
                await self._send_runtime_error(run_id=self.current_run_id or "unknown", message=str(exc), protocol=request.protocol)
            except Exception as exc:
                logger.error("Workflow runtime websocket stream failed: %s", exc, exc_info=True)
                await self._send_runtime_error(run_id=self.current_run_id or "unknown", message=str(exc), protocol=request.protocol)

    async def _attach_live_run(self, run_id: str, *, after_seq: int = 0, protocol: str | None = None):
        async with SessionLocal() as db:
            app_context = AppContext(
                db=db,
                auth=self.auth_context,
                redis_service=self.websocket.app.state.redis_service,
                vector_manager=self.websocket.app.state.vector_manager,
                arq_pool=self.websocket.app.state.arq_pool,
            )
            service = WorkflowService(app_context)
            try:
                bridge = WorkflowProtocolBridgeService(service)
                stream = await bridge.live_stream(run_id=run_id, after_seq=after_seq, protocol=protocol)
                self.current_run_id = stream.run_id
                self.current_thread_id = stream.thread_id
                self.current_trace_id = stream.trace_id
                self.current_parent_run_id = stream.parent_run_id
                async for envelope in stream.generator:
                    await self._send_runtime_envelope(envelope)
            except asyncio.CancelledError:
                raise
            except ServiceException as exc:
                await self._send_runtime_error(run_id=run_id, message=str(exc))
            except Exception as exc:
                logger.error("Workflow websocket live attach failed: %s", exc, exc_info=True)
                await self._send_runtime_error(run_id=run_id, message=str(exc))

    async def _request_run_cancel(self, run_id: str):
        async with SessionLocal() as db:
            app_context = AppContext(
                db=db,
                auth=self.auth_context,
                redis_service=self.websocket.app.state.redis_service,
                vector_manager=self.websocket.app.state.vector_manager,
                arq_pool=self.websocket.app.state.arq_pool,
            )
            service = WorkflowService(app_context)
            await service.cancel_run(run_id)

    async def _send_runtime_envelope(self, envelope):
        try:
            await self.websocket.send_text(envelope.model_dump_json(by_alias=True, exclude_none=True))
        except RuntimeError:
            pass

    async def _send_runtime_error(self, *, run_id: str, message: str, protocol: str | None = None):
        envelope = WorkflowProtocolBridgeService.build_error_envelope(
            run_id=run_id,
            message=message,
            thread_id=self.current_thread_id,
            trace_id=self.current_trace_id,
            parent_run_id=self.current_parent_run_id,
            protocol=protocol,
        )
        await self._send_runtime_envelope(envelope)
