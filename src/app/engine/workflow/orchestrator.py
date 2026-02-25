import asyncio
import time
import re
from collections import deque
from typing import Dict, Any, List, Set, Optional, Deque, Protocol
from async_timeout import timeout

from .definitions import WorkflowGraphDef, WorkflowNode, NodeExecutionResult, NodeResultData, RuntimeStatus, ErrorBody,  ParameterSchema, StreamEvent
from .graph import WorkflowGraph
from .context import WorkflowContext, NodeState
from .registry import default_node_registry, WorkflowRuntimeContext
from .interceptor import NodeExecutionInterceptor, NextCall
from ..utils.parameter_schema_utils import schemas2obj
from ..utils.stream import Streamable

# 定义回调协议，供上层实现
class WorkflowCallbacks(Protocol):
    async def on_execution_start(self, workflow_def: WorkflowGraphDef) -> None: ...
    async def on_node_start(self, state: NodeState) -> None: ...
    async def on_node_finish(self, state: NodeState) -> None: ...
    async def on_node_error(self, state: NodeState) -> None: ...
    async def on_node_skipped(self, state: NodeState) -> None: ...
    async def on_stream_start(self, event: StreamEvent) -> None: ...
    async def on_stream_chunk(self, event: StreamEvent) -> None: ...
    async def on_stream_end(self, event: StreamEvent) -> None: ...
    async def on_execution_end(self, result: NodeResultData) -> None: ...
    async def on_event(self, type: str, data: Any) -> None: ... # 通用 fallback

class WorkflowOrchestrator(WorkflowRuntimeContext):
    def __init__(
        self, 
        workflow_def: WorkflowGraphDef,
        payload: Dict[str, Any] = None,
        callbacks: WorkflowCallbacks = None,
        parent_variables: Dict[str, Any] = None,
        external_context: Any = None,
        interceptors: List[NodeExecutionInterceptor] = None
    ):
        self.graph = WorkflowGraph(workflow_def)
        self.context_mgr = WorkflowContext(payload or {})
        self.callbacks = callbacks
        self._external_context = external_context
        self.interceptors = interceptors or []
        
        if parent_variables:
            self.context_mgr._variables.update(parent_variables)
        
        for node in self.graph.all_nodes:
            self.context_mgr.init_node_state(node.id)
            
        self.execution_queue: Deque[str] = deque([self.graph.start_node_id])
        self.running_tasks: Dict[str, asyncio.Task] = {} 
        self._variable_ref_cache: Dict[str, Dict] = {}
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

    def create_sub_workflow_executor(self, workflow_data: Dict[str, Any], parent_variables: Dict[str, Any], payload: Dict[str, Any] = None) -> 'WorkflowOrchestrator':
        try:
            if isinstance(workflow_data, dict):
                graph_def = WorkflowGraphDef.model_validate(workflow_data)
            else:
                graph_def = workflow_data
        except Exception as e:
            raise ValueError(f"Invalid sub-workflow definition: {e}")

        return WorkflowOrchestrator(
            workflow_def=graph_def,
            payload=payload if payload is not None else self.payload,
            callbacks=self.callbacks,
            parent_variables=parent_variables,
            external_context=self._external_context,
            interceptors=self.interceptors
        )

    def get_ref_details(self, consumer_node_id: str, variable_path: str) -> Optional[Dict]:
        cache_key = f"{consumer_node_id}:{variable_path}"
        if cache_key in self._variable_ref_cache:
            return self._variable_ref_cache[cache_key]

        try:
            consumer_node = self.graph.get_node(consumer_node_id)
        except KeyError:
            return None

        top_level_var = variable_path.split('.')[0]
        inputs_schema = consumer_node.data.inputs
        found_ref = self._find_ref_in_schemas(inputs_schema, top_level_var)
        self._variable_ref_cache[cache_key] = found_ref
        return found_ref

    def _find_ref_in_schemas(self, schemas: List[ParameterSchema], target_name: str) -> Optional[Dict]:
        for item in schemas:
            if item.name == target_name:
                if item.value and item.value.type == 'ref':
                    content = item.value.content
                    if isinstance(content, dict):
                        return content
                return None 

            if item.properties:
                found = self._find_ref_in_schemas(item.properties, target_name)
                if found: return found
            
            if item.items and item.items.properties:
                nested_props = [
                    ParameterSchema(**p.model_dump()) 
                    for p in item.items.properties 
                    if hasattr(p, 'name')
                ]
                found = self._find_ref_in_schemas(nested_props, target_name)
                if found: return found
        return None

    def _identify_stream_producers(self) -> Set[str]:
        producers = set()
        for node in self.graph.all_nodes:
            config = node.data.config
            if config.returnType == 'Text' and config.stream:
                content_template = config.content or ''
                if not content_template: continue
                vars_in_template = re.findall(r'\{\{([^}]+)\}\}', content_template)
                for var_path in vars_in_template:
                    var_path = var_path.strip()
                    ref = self.get_ref_details(node.id, var_path)
                    if ref and 'blockID' in ref:
                        producers.add(ref['blockID'])
        return producers

    async def execute(self) -> NodeResultData:
        await self.send('execution_start', self.graph._def)
        final_result = None

        try:
            while self.execution_queue or self.running_tasks:
                nodes_to_run = []
                while self.execution_queue:
                    node_id = self.execution_queue.popleft()
                    state = self.context_mgr.get_node_state(node_id)
                    if state.status != 'PENDING': continue
                    self.context_mgr.update_node_state(node_id, status='RUNNING')
                    nodes_to_run.append(node_id)

                if nodes_to_run:
                    tasks_to_gather = []
                    for node_id in nodes_to_run:
                        task = asyncio.create_task(self._execute_node_wrapper(node_id))
                        self.running_tasks[node_id] = task
                        tasks_to_gather.append(task)
                    
                    if tasks_to_gather:
                        results = await asyncio.gather(*tasks_to_gather, return_exceptions=True)
                        for res in results:
                            if isinstance(res, list): 
                                self.execution_queue.extend(res)
                            elif isinstance(res, Exception):
                                print(f"[Orchestrator] Task error: {res}")

                if not self.execution_queue and self.running_tasks:
                    self.running_tasks = {nid: t for nid, t in self.running_tasks.items() if not t.done()}
                    if not self.running_tasks: continue
                    done, pending = await asyncio.wait(
                        self.running_tasks.values(),
                        return_when=asyncio.FIRST_COMPLETED
                    )
            
            end_node = self.graph.end_node
            st = self.context_mgr.get_node_state(end_node.id)
            if st.status == 'COMPLETED':
                final_result = st.result
                        
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Orchestrator] Critical engine error: {e}")
            raise e
        finally:
            if self.running_tasks:
                for nid, task in self.running_tasks.items():
                    if not task.done(): 
                        task.cancel()
                        # 如果这是个流式节点，我们还需要取消 broadcaster 内部的任务
                        # 因为 monitor_task 只是在等待 Event，取消它不会自动取消 LLM 生成
                        node_state = self.context_mgr.get_node_state(nid)
                        var = self.context_mgr.variables.get(nid)
                        if isinstance(var, Streamable):
                            await var.cancel() # 显式取消底层生成
                await asyncio.gather(*self.running_tasks.values(), return_exceptions=True)

        if not isinstance(final_result, NodeResultData):
            # 类型不匹配是严重错误，必须报错
            raise TypeError(f"End node must return NodeResultData. ")
        
        await self.send('execution_end', final_result)
        return final_result

    async def _execute_node_wrapper(self, node_id: str) -> List[str]:
        await self.send('node_start', self.context_mgr.get_node_state(node_id))
        node_def = self.graph.get_node(node_id)
        # --- 核心执行逻辑封装 (The Core) ---
        async def core_execution() -> NodeState:
            policy = node_def.data.config.executionPolicy
            retry_times = policy.retryTimes if policy and policy.switch else 0
            timeout_ms = policy.timeoutMs if policy and policy.switch else 180000
            timeout_sec = float(timeout_ms) / 1000.0
            executor_cls = default_node_registry.get(node_def.data.registryId)
            is_producer = node_id in self.stream_producers
            executor = executor_cls(self, node_def, is_producer)
            result: NodeExecutionResult = None
            is_success = False
            last_exc = None
            # 记录统一的开始时间
            s_time = time.time()
            # 以上步骤出错说明是非法节点就别想继续执行和容错了。
            try:
                for attempt in range(retry_times + 1):
                    try:
                        async with timeout(timeout_sec):
                            result = await executor.execute()
                            is_success = True
                            break
                    except Exception as e:
                        last_exc = e
                if not is_success:
                    raise last_exc or Exception("Execution failed with unknown error")
                e_time = time.time() - s_time
                if isinstance(result.data, Streamable):
                    node_state = self.context_mgr.update_node_state(node_id, input=result.input, status='STREAMTASK')
                    # data是流式 Broadcaster
                    broadcaster = result.data
                    self.context_mgr.set_variable(node_id, broadcaster)
                    # 计算启动消耗了多少时间，计算剩余超时时间
                    remaining_timeout = max(0.1, timeout_sec - e_time)
                    
                    # 将 s_time 传进去，以便结束时计算总耗时
                    monitor_task = asyncio.create_task(self._wait_for_stream(node_id, broadcaster, remaining_timeout, s_time))
                    self.running_tasks[node_id] = monitor_task
                    return node_state
                else:
                    # 成功逻辑：注入 runtimeStatus
                    if policy and policy.switch and policy.processType in [2, 3]:
                        result.data.output["runtimeStatus"] = RuntimeStatus(
                            isSuccess=True, errorBody=None
                        ).model_dump()
                    node_state = self.context_mgr.update_node_state(
                        node_id, 
                        status='COMPLETED', 
                        input=result.input,
                        result=result.data, 
                        activated_port=result.activated_port,
                        executed_time=e_time
                    )
                    
                    self.context_mgr.set_variable(node_id, result.data.output)
                    await self.send('node_finish', node_state)
                    return node_state
                
            except Exception as e:
                # D. 失败处理 (Failure Handling within Core)
                # 现在的 _handle_node_failure 返回 NodeState
                return await self._handle_node_failure(node_id, e, policy)
                
        # --- 责任链构建 (The Chain) ---
        # 初始链条就是核心执行逻辑
        chain: NextCall = core_execution

        # 倒序包装：列表最后的一个拦截器，最先包裹 core_execution
        # 列表第一个拦截器（如 Trace），最后包裹，因此它在洋葱的最外层
        for interceptor in reversed(self.interceptors):
            # 使用默认参数捕获闭包变量，防止循环变量泄漏问题
            def wrap(curr=interceptor, nxt=chain):
                return curr.intercept(node_def, self.context_mgr, nxt)
            chain = wrap

        # --- 触发执行 ---
        try:
            final_state = await chain()
            # 调度触发条件
            # 逻辑：只有在成功(COMPLETED) 或 流式任务启动(STREAMTASK) 时触发后续
            # 流任务也继续调度，让不依赖它的节点继续执行，依赖节点会自动等流结果
            # FAILED 状态（且未被策略挽救）不应触发后续
            if final_state.status in ("COMPLETED", "STREAMTASK"):
                return await self._evaluate_successors(node_id)
            return []
        except Exception as e:
            # 理论上 interceptors 和 core 应该捕获所有异常
            # 如果走到这里，说明是系统级严重错误，工作流在该分支直接终止。
            print(f"[Orchestrator] Critical unhandled error in node {node_id}: {e}")
            node_state = self.context_mgr.update_node_state(node_id, status='FAILED', result=NodeResultData(error_msg=str(e)))
            await self.send('node_error', node_state)
            return []

    async def _handle_node_failure(self, node_id: str, error: Exception, policy: Any) -> NodeState:
        process_type = policy.processType if policy and policy.switch else 1
        
        if process_type in [2, 3]:
            node_def = self.graph.get_node(node_id)
            try:
                base_output = await schemas2obj(node_def.data.outputs, self.variables)
            except:
                base_output = {}

            status = RuntimeStatus(
                isSuccess=False,
                errorBody=ErrorBody(
                    message=str(error),
                    type=type(error).__name__,
                    data=policy.dataOnErr or ""
                )
            )

            final_data = NodeResultData(
                output={
                    **base_output,
                    "runtimeStatus": status.model_dump()
                }
            )
            activated_port = "0" if process_type == 2 else "error"

            node_state = self.context_mgr.update_node_state(
                node_id, 
                status='COMPLETED', 
                result=final_data,
                activated_port=activated_port
            )
            self.context_mgr.set_variable(node_id, final_data.output)
            await self.send('node_finish', node_state)
            return node_state
            
        node_state = self.context_mgr.update_node_state(node_id, status='FAILED', result=NodeResultData(error_msg=str(error)))
        await self.send('node_error', node_state)
        return node_state

    async def _wait_for_stream(self, node_id: str, broadcaster: Streamable, timeout_sec: float, start_time: float):
        try:
            # 如果超时，它会抛出 asyncio.TimeoutError 并自动取消内部的 await
            output = await asyncio.wait_for(
                broadcaster.get_result(), 
                timeout=timeout_sec
            )
            # 计算包含启动+流传输的总耗时
            total_duration = time.time() - start_time
            node_state = self.context_mgr.update_node_state(
                node_id, 
                status='COMPLETED', 
                result=NodeResultData(output=output),
                executed_time=total_duration
            )
            self.context_mgr.set_variable(node_id, output)
            await self.send('node_finish', node_state)
            next_nodes = await self._evaluate_successors(node_id)
            self.execution_queue.extend(next_nodes)
        except asyncio.TimeoutError:
            # [超时处理]
            print(f"[Orchestrator] Node {node_id} timed out after {timeout_sec}s.")
            
            # 1. 掐断上游：调用 Broadcaster 的 cancel
            await broadcaster.cancel()
            
            # 2. 标记失败
            node_state = self.context_mgr.update_node_state(
                node_id, 
                status='FAILED',
                result=NodeResultData(error_msg=f"Execution timed out after {timeout_sec}s")
            )
            # 发送错误事件
            await self.send('node_error', node_state)
        except Exception as e:
            print(f"[Orchestrator] Stream task for {node_id} failed: {e}")
            node_state = self.context_mgr.update_node_state(node_id, status='FAILED', result=NodeResultData(error_msg=str(e)))
            await self.send('node_error', node_state)

    async def _evaluate_successors(self, completed_node_id: str) -> List[str]:
        nodes_to_queue = []
        successors = self.graph.get_successors(completed_node_id)
        for succ_id in successors:
            await self._evaluate_single_node(succ_id, nodes_to_queue)
        return nodes_to_queue

    async def _evaluate_single_node(self, node_id: str, queue: List[str]):
        state = self.context_mgr.get_node_state(node_id)
        if state.status != 'PENDING': return

        predecessors = self.graph.get_predecessors(node_id)
        if not predecessors: return

        ready_states = {'COMPLETED', 'SKIPPED', 'STREAMTASK'}
        for pred_id in predecessors:
            pred_state = self.context_mgr.get_node_state(pred_id)
            if pred_state.status not in ready_states: return

        is_active = False
        for pred_id in predecessors:
            pred_state = self.context_mgr.get_node_state(pred_id)
            if pred_state.status in {'COMPLETED', 'STREAMTASK'}:
                targets = self.graph.get_targets_from_port(pred_id, pred_state.activated_port)
                if node_id in targets:
                    is_active = True
                    break 

        if is_active:
            queue.append(node_id)
        else:
            all_predecessors_finished = True
            for pred_id in predecessors:
                pred_state = self.context_mgr.get_node_state(pred_id)
                if pred_state.status not in {'COMPLETED', 'SKIPPED'}:
                    all_predecessors_finished = False
                    break
            
            if all_predecessors_finished:
                node_state = self.context_mgr.update_node_state(node_id, status='SKIPPED')
                await self.send('node_skipped', node_state)
                recursive_new_nodes = await self._evaluate_successors(node_id)
                queue.extend(recursive_new_nodes)