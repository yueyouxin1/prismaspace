# src/app/api/v1/workflow/ws_handler.py

import asyncio
import json
import logging
from typing import Dict, Optional

from pydantic import TypeAdapter

from app.api.websocket.base import BaseWebSocketHandler, WSPacket
from app.core.context import AppContext
from app.db.session import SessionLocal
from app.schemas.protocol import (
    WORKFLOW_RUNTIME_SPEC,
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
from app.services.resource.workflow.protocol_adapter import WorkflowRuntimeProtocolAdapter
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
        self.protocol_adapter = WorkflowRuntimeProtocolAdapter()

    async def _dispatch(self, text: str):
        try:
            payload = json.loads(text)
        except Exception:
            await self.reply_error(None, "Protocol Error: Invalid JSON payload")
            return

        if isinstance(payload, dict) and payload.get("spec") == WORKFLOW_RUNTIME_SPEC and isinstance(payload.get("type"), str):
            try:
                message = _runtime_message_adapter.validate_python(payload)
            except Exception as exc:
                await self._send_runtime_error(run_id="unknown", message=f"Invalid workflow runtime message: {exc}")
                return
            await self._dispatch_runtime_message(message)
            return

        await super()._dispatch(text)

    async def _dispatch_runtime_message(self, message):
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
                self._attach_live_run(message.run_id, after_seq=message.after_seq)
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

    @staticmethod
    def _build_node_index(graph: Dict[str, object] | None) -> Dict[str, Dict[str, str]]:
        node_index: Dict[str, Dict[str, str]] = {}
        if not isinstance(graph, dict):
            return node_index
        for node in graph.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            node_data = node.get("data") or {}
            if not isinstance(node_id, str) or not isinstance(node_data, dict):
                continue
            node_index[node_id] = {
                "registryId": str(node_data.get("registryId") or ""),
                "name": str(node_data.get("name") or node_id),
            }
        return node_index

    async def _run_workflow_stream(self, *, instance_uuid: str, request: WorkflowExecutionRequest, request_id: Optional[str]):
        async with SessionLocal() as db:
            try:
                detached = False
                app_context = AppContext(
                    db=db,
                    auth=self.auth_context,
                    redis_service=self.websocket.app.state.redis_service,
                    vector_manager=self.websocket.app.state.vector_manager,
                    arq_pool=self.websocket.app.state.arq_pool,
                )
                service = WorkflowService(app_context)
                instance = await service.get_by_uuid(instance_uuid)
                if instance is None:
                    await self._send_runtime_error(run_id="unknown", message="Workflow not found")
                    return
                await service._check_execute_perm(instance)
                node_index = self._build_node_index(instance.graph)

                result = await service.async_execute(instance_uuid, request, self.user)
                self.current_run_id = result.run_id
                self.current_thread_id = result.thread_id
                self.current_trace_id = result.trace_id
                self.current_parent_run_id = request.parent_run_id
                self.current_detach = getattr(result, "detach", None)
                await self._send_runtime_envelope(
                    self.protocol_adapter.build_session_ready(
                        run_id=result.run_id,
                        thread_id=result.thread_id,
                        trace_id=result.trace_id,
                        parent_run_id=request.parent_run_id,
                        mode="execute",
                    )
                )

                try:
                    async for event in result.generator:
                        seq = None
                        try:
                            seq = int(event.id) if event.id is not None else None
                        except Exception:
                            seq = None
                        envelope = self.protocol_adapter.build_envelope(
                            event_type=event.event,
                            payload=event.data,
                            run_id=result.run_id,
                            thread_id=result.thread_id,
                            trace_id=result.trace_id,
                            parent_run_id=request.parent_run_id,
                            seq=seq,
                            node_index=node_index,
                        )
                        await self._send_runtime_envelope(envelope)
                except asyncio.CancelledError:
                    detached = True
                    if callable(self.current_detach):
                        self.current_detach()
                    raise
                finally:
                    if result.task and not result.task.done() and not detached:
                        try:
                            await result.task
                        except Exception:
                            pass

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Workflow runtime websocket stream failed: %s", exc, exc_info=True)
                await self._send_runtime_error(run_id=self.current_run_id or "unknown", message=str(exc))

    async def _attach_live_run(self, run_id: str, *, after_seq: int = 0):
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
                run = await service.get_run(run_id)
                instance = await service.get_by_uuid(run.workflow_instance_uuid)
                node_index = self._build_node_index(instance.graph if instance else {})
                self.current_run_id = run.run_id
                self.current_thread_id = run.thread_id
                self.current_trace_id = run.trace_id
                self.current_parent_run_id = run.parent_run_id
                await self._send_runtime_envelope(
                    self.protocol_adapter.build_session_ready(
                        run_id=run.run_id,
                        thread_id=run.thread_id,
                        trace_id=run.trace_id,
                        parent_run_id=run.parent_run_id,
                        mode="live",
                    )
                )
                await self._send_runtime_envelope(
                    self.protocol_adapter.build_run_attached(
                        run_id=run.run_id,
                        thread_id=run.thread_id,
                        trace_id=run.trace_id,
                        parent_run_id=run.parent_run_id,
                        after_seq=after_seq,
                    )
                )
                async for live_envelope in service.stream_live_run_events(run_id, after_seq=after_seq):
                    payload = live_envelope.get("payload", {})
                    envelope = self.protocol_adapter.build_envelope(
                        event_type=str(payload.get("event", "message")),
                        payload=payload.get("data", {}) if isinstance(payload.get("data"), dict) else {"value": payload.get("data")},
                        run_id=run.run_id,
                        thread_id=run.thread_id,
                        trace_id=run.trace_id,
                        parent_run_id=run.parent_run_id,
                        seq=int(live_envelope.get("seq", 0)) if live_envelope.get("seq") is not None else None,
                        node_index=node_index,
                    )
                    await self._send_runtime_envelope(envelope)
            except asyncio.CancelledError:
                raise
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

    async def _send_runtime_error(self, *, run_id: str, message: str):
        envelope = self.protocol_adapter.build_envelope(
            event_type="run.failed",
            payload={"error": message},
            run_id=run_id,
            thread_id=self.current_thread_id,
            trace_id=self.current_trace_id,
            parent_run_id=self.current_parent_run_id,
        )
        await self._send_runtime_envelope(envelope)
