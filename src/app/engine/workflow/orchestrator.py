import asyncio
import re
import time
from collections import deque
from typing import Any, Dict, List, Optional, Protocol, Set

from async_timeout import timeout

from .context import NodeState, WorkflowContext, WorkflowRuntimeSnapshot
from .definitions import (
    ErrorBody,
    NodeExecutionResult,
    NodeResultData,
    ParameterSchema,
    RuntimeStatus,
    StreamEvent,
    WorkflowInterrupt,
    WorkflowInterruptSignal,
)
from .interceptor import NextCall, NodeExecutionInterceptor
from .registry import WorkflowRuntimeContext, default_node_registry
from .runtime_ir import WorkflowRuntimeNodeSpec, WorkflowRuntimePlan
from ..utils.parameter_schema_utils import schemas2obj
from ..utils.stream import Streamable


class WorkflowCallbacks(Protocol):
    async def on_execution_start(self, workflow_plan: WorkflowRuntimePlan) -> None: ...
    async def on_node_start(self, state: NodeState) -> None: ...
    async def on_node_finish(self, state: NodeState) -> None: ...
    async def on_node_error(self, state: NodeState) -> None: ...
    async def on_node_skipped(self, state: NodeState) -> None: ...
    async def on_stream_start(self, event: StreamEvent) -> None: ...
    async def on_stream_chunk(self, event: StreamEvent) -> None: ...
    async def on_stream_end(self, event: StreamEvent) -> None: ...
    async def on_execution_end(self, result: NodeResultData) -> None: ...
    async def on_event(self, type: str, data: Any) -> None: ...


class WorkflowRuntimeObserver(Protocol):
    async def on_execution_start(
        self,
        workflow_plan: WorkflowRuntimePlan,
        snapshot: WorkflowRuntimeSnapshot,
    ) -> None: ...

    async def on_execution_end(
        self,
        result: Optional[NodeResultData],
        snapshot: WorkflowRuntimeSnapshot,
        status: str,
    ) -> None: ...

    async def on_node_state(
        self,
        node: WorkflowRuntimeNodeSpec,
        state: NodeState,
        reason: str,
        snapshot: Optional[WorkflowRuntimeSnapshot] = None,
    ) -> None: ...

    async def should_cancel(self) -> bool: ...


class WorkflowOrchestrator(WorkflowRuntimeContext):
    def __init__(
        self,
        workflow_plan: WorkflowRuntimePlan,
        payload: Dict[str, Any] = None,
        callbacks: WorkflowCallbacks = None,
        parent_variables: Dict[str, Any] = None,
        external_context: Any = None,
        interceptors: List[NodeExecutionInterceptor] = None,
        restored_snapshot: Optional[WorkflowRuntimeSnapshot] = None,
        runtime_observer: Optional[WorkflowRuntimeObserver] = None,
    ):
        self.plan = workflow_plan
        self.callbacks = callbacks
        self._external_context = external_context
        self.interceptors = interceptors or []
        self.runtime_observer = runtime_observer

        if restored_snapshot is not None:
            self.context_mgr = WorkflowContext.from_snapshot(restored_snapshot)
            self.execution_queue = deque(restored_snapshot.ready_queue or [])
            self._checkpoint_step = restored_snapshot.step_index
        else:
            self.context_mgr = WorkflowContext(payload or {})
            self.execution_queue = deque([self.plan.start_node_id])
            self._checkpoint_step = 0

        if parent_variables:
            self.context_mgr.variables.update(parent_variables)

        for node in self.plan.all_nodes:
            self.context_mgr.init_node_state(node.id)

        self.running_tasks: Dict[str, asyncio.Task] = {}
        self._task_node_ids: Dict[asyncio.Task, str] = {}
        self._variable_ref_cache: Dict[str, Optional[Dict[str, str]]] = {}
        self.stream_producers = self._identify_stream_producers()

    @property
    def variables(self) -> Dict[str, Any]:
        return self.context_mgr.variables

    @property
    def payload(self) -> Dict[str, Any]:
        return self.context_mgr.payload

    @property
    def version(self) -> int:
        return self.context_mgr.version

    @property
    def external_context(self) -> Any:
        return self._external_context

    async def send(self, type: str, data: Any = None):
        if self.callbacks:
            method_name = f"on_{type}"
            if hasattr(self.callbacks, method_name):
                await getattr(self.callbacks, method_name)(data)
            elif hasattr(self.callbacks, "on_event"):
                await self.callbacks.on_event(type, data)

    def create_sub_workflow_executor(
        self,
        workflow_data: Dict[str, Any],
        parent_variables: Dict[str, Any],
        payload: Dict[str, Any] = None,
    ) -> "WorkflowOrchestrator":
        from .runtime_ir import WorkflowRuntimeCompiler

        runtime_plan = WorkflowRuntimeCompiler().compile(workflow_data)
        return WorkflowOrchestrator(
            workflow_plan=runtime_plan,
            payload=payload if payload is not None else self.payload,
            callbacks=self.callbacks,
            parent_variables=parent_variables,
            external_context=self._external_context,
            interceptors=self.interceptors,
            runtime_observer=None,
        )

    def get_ref_details(self, consumer_node_id: str, variable_path: str) -> Optional[Dict[str, str]]:
        cache_key = f"{consumer_node_id}:{variable_path}"
        if cache_key in self._variable_ref_cache:
            return self._variable_ref_cache[cache_key]

        try:
            consumer_node = self.plan.get_node(consumer_node_id)
        except KeyError:
            return None

        top_level_var = variable_path.split(".")[0]
        found_ref = self._find_ref_in_schemas(consumer_node.inputs, top_level_var)
        self._variable_ref_cache[cache_key] = found_ref
        return found_ref

    def _find_ref_in_schemas(
        self,
        schemas: List[ParameterSchema],
        target_name: str,
    ) -> Optional[Dict[str, str]]:
        for item in schemas:
            if item.name == target_name:
                if item.value and item.value.type == "ref":
                    content = item.value.content
                    if isinstance(content, dict):
                        return {
                            "blockID": str(content.get("blockID", "")),
                            "path": str(content.get("path", "")),
                            "source": str(content.get("source", "")),
                        }
                return None

            if item.properties:
                found = self._find_ref_in_schemas(item.properties, target_name)
                if found:
                    return found

            if item.items and item.items.properties:
                found = self._find_ref_in_schemas(item.items.properties, target_name)
                if found:
                    return found
        return None

    def _identify_stream_producers(self) -> Set[str]:
        producers = set()
        for node in self.plan.all_nodes:
            if node.config.get("returnType") != "Text" or not node.config.get("stream"):
                continue
            content_template = node.config.get("content") or ""
            if not content_template:
                continue
            vars_in_template = re.findall(r"\{\{([^}]+)\}\}", content_template)
            for var_path in vars_in_template:
                ref = self.get_ref_details(node.id, var_path.strip())
                if ref and ref.get("blockID"):
                    producers.add(ref["blockID"])
        return producers

    def _snapshot(self) -> WorkflowRuntimeSnapshot:
        return self.context_mgr.snapshot(list(self.execution_queue), self._checkpoint_step)

    def _next_snapshot(self) -> WorkflowRuntimeSnapshot:
        self._checkpoint_step += 1
        return self._snapshot()

    async def _should_cancel(self) -> bool:
        if self.runtime_observer is None:
            return False
        return await self.runtime_observer.should_cancel()

    async def execute(self) -> NodeResultData:
        await self.send("execution_start", self.plan)
        if self.runtime_observer:
            await self.runtime_observer.on_execution_start(self.plan, self._snapshot())

        final_result: Optional[NodeResultData] = None
        execution_status = "running"

        try:
            while self.execution_queue or self.running_tasks:
                if await self._should_cancel():
                    execution_status = "cancelled"
                    raise asyncio.CancelledError()

                while self.execution_queue:
                    node_id = self.execution_queue.popleft()
                    state = self.context_mgr.get_node_state(node_id)
                    if state.status != "PENDING":
                        continue
                    if await self._should_cancel():
                        execution_status = "cancelled"
                        raise asyncio.CancelledError()

                    self.context_mgr.update_node_state(node_id, status="RUNNING")
                    task = asyncio.create_task(self._execute_node_wrapper(node_id))
                    self.running_tasks[node_id] = task
                    self._task_node_ids[task] = node_id

                if not self.running_tasks:
                    continue

                done, _ = await asyncio.wait(
                    list(self.running_tasks.values()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    node_id = self._task_node_ids.pop(task, None)
                    if node_id and self.running_tasks.get(node_id) is task:
                        self.running_tasks.pop(node_id, None)

                    if task.cancelled():
                        execution_status = "cancelled"
                        raise asyncio.CancelledError()

                    exc = task.exception()
                    if exc is not None:
                        if isinstance(exc, WorkflowInterruptSignal):
                            execution_status = "interrupted"
                            raise exc
                        if isinstance(exc, asyncio.CancelledError):
                            execution_status = "cancelled"
                            raise exc
                        raise exc

            end_state = self.context_mgr.get_node_state(self.plan.end_node_id)
            if end_state.status == "COMPLETED":
                final_result = end_state.result
                execution_status = "succeeded"
            else:
                execution_status = "failed"
                raise RuntimeError(end_state.result.error_msg or "Workflow finished without a completed End node.")

        except asyncio.CancelledError:
            execution_status = "cancelled"
            raise
        except WorkflowInterruptSignal:
            execution_status = "interrupted"
            raise
        except Exception:
            execution_status = "failed"
            raise
        finally:
            await self._cancel_remaining_tasks()
            if self.runtime_observer:
                await self.runtime_observer.on_execution_end(
                    final_result,
                    self._next_snapshot(),
                    execution_status,
                )

        if not isinstance(final_result, NodeResultData):
            raise TypeError("End node must return NodeResultData.")

        await self.send("execution_end", final_result)
        return final_result

    async def _cancel_remaining_tasks(self) -> None:
        if not self.running_tasks:
            return

        tasks = list(self.running_tasks.values())
        self.running_tasks.clear()
        self._task_node_ids = {
            task: node_id
            for task, node_id in self._task_node_ids.items()
            if task not in tasks
        }
        for task in tasks:
            if not task.done():
                task.cancel()

        for node_id, value in list(self.context_mgr.variables.items()):
            if isinstance(value, Streamable):
                await value.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute_node_wrapper(self, node_id: str) -> None:
        node_spec = self.plan.get_node(node_id)
        await self.send("node_start", self.context_mgr.get_node_state(node_id))
        if self.runtime_observer:
            await self.runtime_observer.on_node_state(
                node_spec,
                self.context_mgr.get_node_state(node_id),
                "node_start",
            )

        node_def = node_spec.to_workflow_node()

        async def core_execution() -> NodeState:
            policy = node_def.data.config.executionPolicy
            retry_times = policy.retryTimes if policy and policy.switch else 0
            timeout_ms = policy.timeoutMs if policy and policy.switch else 180000
            timeout_sec = float(timeout_ms) / 1000.0
            executor_cls = default_node_registry.get(node_def.data.registryId)
            executor = executor_cls(self, node_def, node_id in self.stream_producers)

            result: Optional[NodeExecutionResult] = None
            last_exc: Optional[Exception] = None
            started_at = time.time()

            try:
                for _ in range(retry_times + 1):
                    try:
                        async with timeout(timeout_sec):
                            result = await executor.execute()
                            break
                    except Exception as exc:
                        last_exc = exc

                if result is None:
                    raise last_exc or RuntimeError("Execution failed with unknown error")

                executed_time = time.time() - started_at
                if isinstance(result.data, Streamable):
                    node_state = self.context_mgr.update_node_state(
                        node_id,
                        input=result.input,
                        status="STREAMTASK",
                    )
                    self.context_mgr.set_variable(node_id, result.data)
                    if self.runtime_observer:
                        await self.runtime_observer.on_node_state(
                            node_spec,
                            node_state,
                            "node_streamtask",
                        )

                    remaining_timeout = max(0.1, timeout_sec - executed_time)
                    monitor_task = asyncio.create_task(
                        self._wait_for_stream(
                            node_id=node_id,
                            node_spec=node_spec,
                            broadcaster=result.data,
                            timeout_sec=remaining_timeout,
                            started_at=started_at,
                        )
                    )
                    self.running_tasks[node_id] = monitor_task
                    self._task_node_ids[monitor_task] = node_id
                    return node_state

                if policy and policy.switch and policy.processType in [2, 3]:
                    result.data.output["runtimeStatus"] = RuntimeStatus(
                        isSuccess=True,
                        errorBody=None,
                    ).model_dump()

                node_state = self.context_mgr.update_node_state(
                    node_id,
                    status="COMPLETED",
                    input=result.input,
                    result=result.data,
                    activated_port=result.activated_port,
                    executed_time=executed_time,
                )
                self.context_mgr.set_variable(node_id, result.data.output)
                await self.send("node_finish", node_state)
                return node_state

            except Exception as exc:
                return await self._handle_node_failure(node_id, exc, policy)

        chain: NextCall = core_execution
        for interceptor in reversed(self.interceptors):
            def wrap(curr=interceptor, nxt=chain):
                return curr.intercept(node_def, self.context_mgr, nxt)

            chain = wrap

        final_state = await chain()

        if final_state.status == "COMPLETED":
            await self._queue_successors(node_id)
            if self.runtime_observer:
                await self.runtime_observer.on_node_state(
                    node_spec,
                    final_state,
                    "node_completed",
                    self._next_snapshot(),
                )
            return

        if final_state.status == "STREAMTASK":
            await self._queue_successors(node_id)
            return

        if final_state.status == "FAILED" and self.runtime_observer:
            await self.runtime_observer.on_node_state(
                node_spec,
                final_state,
                "node_failed",
                self._next_snapshot(),
            )
            return

        if final_state.status == "INTERRUPTED":
            if self.runtime_observer:
                await self.runtime_observer.on_node_state(
                    node_spec,
                    final_state,
                    "node_interrupted",
                    self._next_snapshot(),
                )
            interrupt_payload = final_state.result.output.get("interrupt", {}) if final_state.result else {}
            raise WorkflowInterruptSignal(WorkflowInterrupt.model_validate(interrupt_payload))

    async def _handle_node_failure(self, node_id: str, error: Exception, policy: Any) -> NodeState:
        if isinstance(error, WorkflowInterruptSignal):
            interrupt_payload = error.interrupt.model_dump(mode="json")
            node_state = self.context_mgr.update_node_state(
                node_id,
                status="INTERRUPTED",
                result=NodeResultData(output={"interrupt": interrupt_payload}),
            )
            await self.send("interrupt", {"interrupt": interrupt_payload, "node_id": node_id})
            return node_state

        process_type = policy.processType if policy and policy.switch else 1

        if process_type in [2, 3]:
            node_spec = self.plan.get_node(node_id)
            try:
                base_output = await schemas2obj(node_spec.outputs, self.variables)
            except Exception:
                base_output = {}

            status = RuntimeStatus(
                isSuccess=False,
                errorBody=ErrorBody(
                    message=str(error),
                    type=type(error).__name__,
                    data=policy.dataOnErr or "",
                ),
            )
            final_data = NodeResultData(
                output={
                    **base_output,
                    "runtimeStatus": status.model_dump(),
                }
            )
            activated_port = "0" if process_type == 2 else "error"
            node_state = self.context_mgr.update_node_state(
                node_id,
                status="COMPLETED",
                result=final_data,
                activated_port=activated_port,
            )
            self.context_mgr.set_variable(node_id, final_data.output)
            await self.send("node_finish", node_state)
            return node_state

        node_state = self.context_mgr.update_node_state(
            node_id,
            status="FAILED",
            result=NodeResultData(error_msg=str(error)),
        )
        await self.send("node_error", node_state)
        return node_state

    async def _wait_for_stream(
        self,
        *,
        node_id: str,
        node_spec: WorkflowRuntimeNodeSpec,
        broadcaster: Streamable,
        timeout_sec: float,
        started_at: float,
    ) -> None:
        try:
            output = await asyncio.wait_for(broadcaster.get_result(), timeout=timeout_sec)
            total_duration = time.time() - started_at
            node_state = self.context_mgr.update_node_state(
                node_id,
                status="COMPLETED",
                result=NodeResultData(output=output),
                executed_time=total_duration,
            )
            self.context_mgr.set_variable(node_id, output)
            await self.send("node_finish", node_state)
            await self._queue_successors(node_id)
            if self.runtime_observer:
                await self.runtime_observer.on_node_state(
                    node_spec,
                    node_state,
                    "node_completed",
                    self._next_snapshot(),
                )
        except asyncio.TimeoutError:
            await broadcaster.cancel()
            node_state = self.context_mgr.update_node_state(
                node_id,
                status="FAILED",
                result=NodeResultData(error_msg=f"Execution timed out after {timeout_sec}s"),
            )
            await self.send("node_error", node_state)
            if self.runtime_observer:
                await self.runtime_observer.on_node_state(
                    node_spec,
                    node_state,
                    "node_failed",
                    self._next_snapshot(),
                )
        except Exception as exc:
            node_state = self.context_mgr.update_node_state(
                node_id,
                status="FAILED",
                result=NodeResultData(error_msg=str(exc)),
            )
            await self.send("node_error", node_state)
            if self.runtime_observer:
                await self.runtime_observer.on_node_state(
                    node_spec,
                    node_state,
                    "node_failed",
                    self._next_snapshot(),
                )

    async def _queue_successors(self, completed_node_id: str) -> None:
        next_nodes = await self._evaluate_successors(completed_node_id)
        self.execution_queue.extend(next_nodes)

    async def _evaluate_successors(self, completed_node_id: str) -> List[str]:
        nodes_to_queue: List[str] = []
        for succ_id in self.plan.get_successors(completed_node_id):
            await self._evaluate_single_node(succ_id, nodes_to_queue)
        return nodes_to_queue

    async def _evaluate_single_node(self, node_id: str, queue: List[str]) -> None:
        state = self.context_mgr.get_node_state(node_id)
        if state.status != "PENDING":
            return

        predecessors = self.plan.get_predecessors(node_id)
        if not predecessors:
            if node_id not in queue:
                queue.append(node_id)
            return

        ready_states = {"COMPLETED", "SKIPPED", "STREAMTASK"}
        if any(self.context_mgr.get_node_state(pred_id).status not in ready_states for pred_id in predecessors):
            return

        is_active = False
        for pred_id in predecessors:
            pred_state = self.context_mgr.get_node_state(pred_id)
            if pred_state.status in {"COMPLETED", "STREAMTASK"}:
                targets = self.plan.get_targets_from_port(pred_id, pred_state.activated_port)
                if node_id in targets:
                    is_active = True
                    break

        if is_active:
            if node_id not in queue:
                queue.append(node_id)
            return

        all_predecessors_finished = all(
            self.context_mgr.get_node_state(pred_id).status in {"COMPLETED", "SKIPPED"}
            for pred_id in predecessors
        )
        if not all_predecessors_finished:
            return

        node_state = self.context_mgr.update_node_state(node_id, status="SKIPPED")
        await self.send("node_skipped", node_state)
        recursive_new_nodes = await self._evaluate_successors(node_id)
        queue.extend(recursive_new_nodes)
        if self.runtime_observer:
            await self.runtime_observer.on_node_state(
                self.plan.get_node(node_id),
                node_state,
                "node_skipped",
                self._next_snapshot(),
            )
