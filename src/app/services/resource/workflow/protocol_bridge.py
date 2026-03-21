from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from app.services.exceptions import ServiceException
from app.services.resource.workflow.protocol_adapter import get_workflow_protocol_adapter
from app.services.resource.workflow.types.workflow import WorkflowProtocolEnvelopeStream


class WorkflowProtocolBridgeService:
    def __init__(self, workflow_service):
        self.workflow_service = workflow_service

    @staticmethod
    def resolve_adapter(protocol: str | None):
        try:
            return get_workflow_protocol_adapter(protocol)
        except NotImplementedError as exc:
            raise ServiceException(str(exc)) from exc

    def _resolve_adapter(self, protocol: str | None):
        resolver = getattr(self.workflow_service, "resolve_protocol_adapter", None)
        if callable(resolver):
            return resolver(protocol)
        return self.resolve_adapter(protocol)

    def iter_sse(self, stream: WorkflowProtocolEnvelopeStream) -> AsyncGenerator[str, None]:
        adapter = self._resolve_adapter(stream.protocol)

        async def _generator() -> AsyncGenerator[str, None]:
            async for envelope in stream.generator:
                yield adapter.to_sse(envelope)

        return _generator()

    @staticmethod
    def build_error_envelope(
        *,
        run_id: str,
        message: str,
        thread_id: str | None = None,
        trace_id: str | None = None,
        parent_run_id: str | None = None,
        protocol: str | None = None,
    ):
        adapter = WorkflowProtocolBridgeService.resolve_adapter(protocol)
        return adapter.build_envelope(
            event_type="run.failed",
            payload={"error": message},
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            parent_run_id=parent_run_id,
        )

    async def execute_stream(
        self,
        *,
        instance_uuid: str,
        request,
        actor,
        runtime_workspace=None,
    ) -> WorkflowProtocolEnvelopeStream:
        adapter = self._resolve_adapter(request.protocol)
        run_result = await self.workflow_service.async_execute(
            instance_uuid,
            request,
            actor,
            runtime_workspace,
        )

        async def _generator():
            detached = False
            detach_fn = getattr(run_result, "detach", None)
            try:
                yield adapter.build_session_ready(
                    run_id=run_result.run_id,
                    thread_id=run_result.thread_id,
                    trace_id=run_result.trace_id,
                    parent_run_id=request.parent_run_id,
                    mode="execute",
                )
                async for event in run_result.generator:
                    seq = None
                    try:
                        seq = int(event.id) if event.id is not None else None
                    except Exception:
                        seq = None
                    yield adapter.build_envelope(
                        event_type=event.event,
                        payload=event.data,
                        run_id=run_result.run_id,
                        thread_id=run_result.thread_id,
                        trace_id=run_result.trace_id,
                        parent_run_id=request.parent_run_id,
                        seq=seq,
                    )
            except GeneratorExit:
                detached = True
                if callable(detach_fn):
                    detach_fn()
                raise
            except asyncio.CancelledError:
                detached = True
                if callable(detach_fn):
                    detach_fn()
                raise
            finally:
                if run_result.task and not run_result.task.done() and not detached:
                    try:
                        await run_result.task
                    except Exception:
                        pass

        return WorkflowProtocolEnvelopeStream(
            protocol=adapter.protocol,
            generator=_generator(),
            run_id=run_result.run_id,
            thread_id=run_result.thread_id,
            trace_id=run_result.trace_id,
            parent_run_id=request.parent_run_id,
            task=run_result.task,
            detach=getattr(run_result, "detach", None),
        )

    async def debug_stream(
        self,
        *,
        instance_uuid: str,
        node_id: str,
        request,
        actor,
        runtime_workspace=None,
    ) -> WorkflowProtocolEnvelopeStream:
        debug_request = await self.workflow_service.build_debug_node_request(
            instance_uuid=instance_uuid,
            node_id=node_id,
            execute_params=request,
        )
        stream = await self.execute_stream(
            instance_uuid=instance_uuid,
            request=debug_request,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )
        adapter = self._resolve_adapter(debug_request.protocol)
        upstream_generator = stream.generator

        async def _generator():
            async for envelope in upstream_generator:
                if envelope.type == "session.ready":
                    yield adapter.build_session_ready(
                        run_id=stream.run_id or envelope.run_id,
                        thread_id=stream.thread_id or envelope.thread_id,
                        trace_id=stream.trace_id or envelope.trace_id,
                        parent_run_id=stream.parent_run_id,
                        mode="debug",
                        seq=envelope.seq,
                    )
                    continue
                yield envelope

        stream.generator = _generator()
        stream.protocol = adapter.protocol
        return stream

    async def replay_stream(
        self,
        *,
        run_id: str,
        limit: int = 1000,
        protocol: str | None = None,
    ) -> WorkflowProtocolEnvelopeStream:
        adapter = self._resolve_adapter(protocol)
        run = await self.workflow_service.get_run(run_id)

        async def _generator():
            events = await self.workflow_service.list_run_events(run_id, limit=limit)
            yield adapter.build_session_ready(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                mode="replay",
            )
            for event in events:
                yield adapter.build_envelope(
                    event_type=event.event_type,
                    payload=event.payload,
                    run_id=run.run_id,
                    thread_id=run.thread_id,
                    trace_id=run.trace_id,
                    parent_run_id=run.parent_run_id,
                    seq=event.sequence_no,
                    ts=event.created_at,
                )
            yield adapter.build_replay_completed(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                count=len(events),
                limit=limit,
            )

        return WorkflowProtocolEnvelopeStream(
            protocol=adapter.protocol,
            generator=_generator(),
            run_id=run.run_id,
            thread_id=run.thread_id,
            trace_id=run.trace_id,
            parent_run_id=run.parent_run_id,
        )

    async def live_stream(
        self,
        *,
        run_id: str,
        after_seq: int = 0,
        protocol: str | None = None,
    ) -> WorkflowProtocolEnvelopeStream:
        adapter = self._resolve_adapter(protocol)
        run = await self.workflow_service.get_run(run_id)

        async def _generator():
            yield adapter.build_session_ready(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                mode="live",
            )
            yield adapter.build_run_attached(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                after_seq=after_seq,
            )
            async for live_envelope in self.workflow_service.stream_live_run_events(run_id, after_seq=after_seq):
                payload = live_envelope.get("payload", {})
                yield adapter.build_envelope(
                    event_type=str(payload.get("event", "message")),
                    payload=payload.get("data", {}) if isinstance(payload.get("data"), dict) else {"value": payload.get("data")},
                    run_id=run.run_id,
                    thread_id=run.thread_id,
                    trace_id=run.trace_id,
                    parent_run_id=run.parent_run_id,
                    seq=int(live_envelope.get("seq", 0)) if live_envelope.get("seq") is not None else None,
                )

        return WorkflowProtocolEnvelopeStream(
            protocol=adapter.protocol,
            generator=_generator(),
            run_id=run.run_id,
            thread_id=run.thread_id,
            trace_id=run.trace_id,
            parent_run_id=run.parent_run_id,
        )
