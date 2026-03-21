# src/app/services/resource/workflow/workflow_service.py

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Dict, Any, List, Optional, Set, Tuple
from sqlalchemy import func
from pydantic import BaseModel, Field, ConfigDict, ValidationError

from app.core.context import AppContext
from app.core.trace_manager import TraceManager
from app.db.session import SessionLocal
from app.utils.async_generator import AsyncGeneratorManager
from app.models import (
    ResourceExecutionStatus,
    User,
    Workspace,
    Resource,
    Workflow,
    VersionStatus,
)
from app.dao.resource.workflow.workflow_dao import WorkflowDao, WorkflowNodeDefDao
from app.dao.resource.resource_ref_dao import ResourceRefDao
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowCheckpointRead,
    WorkflowEvent,
    WorkflowEventRead,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    WorkflowExecutionResponseData,
    WorkflowInterruptRead,
    WorkflowRead,
    WorkflowRunNodeRead,
    WorkflowRunRead,
    WorkflowRunSummaryRead,
    WorkflowUpdate,
)
from app.schemas.resource.resource_ref_schemas import ReferenceCreate
from app.services.resource.base.base_impl_service import register_service, ResourceImplementationService, ValidationResult, DependencyInfo
from app.services.auditing.types.attributes import WorkflowAttributes
from app.services.resource.workflow.interceptors import WorkflowTraceInterceptor
from app.services.resource.execution.execution_ledger_service import ExecutionLedgerService
from app.services.resource.resource_ref_service import ResourceRefService
from app.services.exceptions import ServiceException, NotFoundError
from app.services.resource.workflow.runtime_persistence import (
    WorkflowDurableRuntimeObserver,
    WorkflowRuntimePersistenceService,
)
from app.services.resource.workflow.event_log_service import WorkflowEventLogService
from app.services.resource.workflow.live_events import WorkflowLiveEventService
from app.services.resource.workflow.runtime_runner import WorkflowRuntimeRunner
from app.services.resource.workflow.runtime_registry import WorkflowTaskRegistry
from app.services.resource.workflow.types.workflow import PreparedWorkflowRun, WorkflowRunResult
# Engine Imports
from app.engine.workflow import (
    NodeResultData,
    NodeState,
    ParameterSchema,
    StreamEvent,
    WorkflowCallbacks,
    WorkflowEngineService,
    WorkflowGraph,
    WorkflowGraphDef,
    WorkflowInterruptSignal,
    WorkflowRuntimeCompiler,
    WorkflowRuntimePlan,
    WorkflowRuntimeSnapshot,
)
from app.engine.utils.parameter_schema_utils import build_json_schema_node
from app.engine.schemas.parameter_schema import RefValue, ValueRefContent
from app.engine.model.llm import LLMTool, LLMToolFunction

logger = logging.getLogger(__name__)

class ExternalContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    app_context: AppContext = Field(..., description="运行时请求上下文")
    workflow_instance: Workflow = Field(..., description="当前工作流实例")
    runtime_workspace: Workspace = Field(..., description="运行时工作空间")
    trace_id: Optional[str] = Field(None, description="Trace ID")
    run_id: Optional[str] = Field(None, description="Execution Run ID")
    thread_id: Optional[str] = Field(None, description="Execution Thread ID")
    resume_payload: Optional[Dict[str, Any]] = Field(None, description="Resume payload injected on interrupted run recovery")

class WorkflowStreamCallbacks(WorkflowCallbacks):
    """
    [Production Adapter]
    将 Workflow 引擎的内部生命周期事件转换为统一的异步队列事件。
    使用 queue.put_nowait 配合无限容量队列，防止回调阻塞引擎。
    """
    def __init__(
        self,
        generator_manager: AsyncGeneratorManager,
        trace_id: str,
        run_id: str,
        thread_id: str,
        event_persister=None,
    ):
        self.generator_manager = generator_manager
        self.trace_id = trace_id
        self.run_id = run_id
        self.thread_id = thread_id
        self.event_persister = event_persister
        self.live_event_buffer = None

    def bind_live_event_buffer(self, live_event_buffer) -> None:
        self.live_event_buffer = live_event_buffer

    async def _safe_put(self, event: WorkflowEvent):
        try:
            if self.live_event_buffer is not None:
                envelope = await self.live_event_buffer.publish(
                    {
                        "event": event.event,
                        "data": event.data,
                    }
                )
                event.id = str(envelope["seq"])
            if self.event_persister is not None:
                await self.event_persister(event.event, event.data)
            self.generator_manager.put_nowait(event)
        except Exception as e:
            logger.error(f"Failed to put event to queue: {e}")

    async def on_execution_start(self, workflow_def: WorkflowRuntimePlan) -> None:
        await self._safe_put(
            WorkflowEvent(
                event="run.started",
                data={
                    "trace_id": self.trace_id,
                    "run_id": self.run_id,
                    "thread_id": self.thread_id,
                },
            )
        )

    async def on_node_start(self, state: NodeState) -> None:
        await self._safe_put(WorkflowEvent(event="node.started", data=state.model_dump()))

    async def on_node_finish(self, state: NodeState) -> None:
        await self._safe_put(WorkflowEvent(event="node.completed", data=state.model_dump()))

    async def on_node_error(self, state: NodeState) -> None:
        await self._safe_put(WorkflowEvent(event="node.failed", data=state.model_dump()))

    async def on_node_skipped(self, state: NodeState) -> None:
        await self._safe_put(WorkflowEvent(event="node.skipped", data=state.model_dump()))

    async def on_stream_start(self, event: StreamEvent) -> None:
        await self._safe_put(WorkflowEvent(event="stream.started", data=event.model_dump()))

    async def on_stream_chunk(self, event: StreamEvent) -> None:
        await self._safe_put(WorkflowEvent(event="stream.delta", data=event.model_dump()))
        
    async def on_stream_end(self, event: StreamEvent) -> None:
        await self._safe_put(WorkflowEvent(event="stream.finished", data=event.model_dump()))

    async def on_execution_end(self, result: NodeResultData) -> None:
        payload = result.model_dump(mode="json")
        payload.update({"run_id": self.run_id, "thread_id": self.thread_id})
        await self._safe_put(WorkflowEvent(event="run.finished", data=payload))

    async def on_event(self, type: str, data: Any) -> None:
        payload = data if isinstance(data, dict) else {"detail": data}
        payload.setdefault("run_id", self.run_id)
        payload.setdefault("thread_id", self.thread_id)
        await self._safe_put(WorkflowEvent(event=type, data=payload))

@register_service
class WorkflowService(ResourceImplementationService):
    name: str = "workflow"

    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = WorkflowDao(context.db)
        self.node_dao = WorkflowNodeDefDao(context.db)
        self.ref_service = ResourceRefService(context)
        self.ref_dao = ResourceRefDao(context.db)
        self.engine_service = WorkflowEngineService()
        self.execution_ledger_service = ExecutionLedgerService(context)
        self.runtime_persistence = WorkflowRuntimePersistenceService(context)
        self.event_log_service = WorkflowEventLogService(context)
        self.live_event_service = WorkflowLiveEventService(context)
        self.runtime_compiler = WorkflowRuntimeCompiler()
        self._db_session_factory = context.db_session_factory or SessionLocal

    # ==========================================================================
    # 2. CRUD & Lifecycle
    # ==========================================================================
    async def list_node_defs(self):
        return await self.node_dao.get_list(where={"is_active": True}, order=["display_order", "id"])

    async def get_by_uuid(self, instance_uuid: str) -> Optional[Workflow]:
        return await self.dao.get_by_uuid(instance_uuid)

    async def create_instance(self, resource: Resource, actor: User) -> Workflow:
        # 默认初始化一个最简单的有效图：Start -> End
        initial_graph = {
            "nodes": [
                {
                    "id": "start",
                    "data": {
                        "registryId": "Start",
                        "name": "Start", 
                        "inputs": [], 
                        "outputs": [],
                        "config": {}
                    },
                    "position": {"x": 100, "y": 200}
                },
                {
                    "id": "end",
                    "data": {
                        "registryId": "End",
                        "name": "End", 
                        "inputs": [], 
                        "outputs": [],
                        "config": {"returnType": "Object"}
                    },
                    "position": {"x": 500, "y": 200}
                }
            ],
            "edges": [
                {"sourceNodeID": "start", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"}
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1}
        }
        
        instance = Workflow(
            version_tag="__workspace__",
            status=VersionStatus.WORKSPACE,
            creator_id=actor.id,
            resource_type=self.name,
            name=resource.name,
            description=resource.description,
            resource=resource,
            graph=initial_graph,
            inputs_schema=[],
            outputs_schema=[],
            is_stream=False
        )
        return instance

    async def update_instance(self, instance: Workflow, update_data: Dict[str, Any]) -> Workflow:
        """
        [Hardened] 更新 Workflow 实例。
        包含：图结构校验、契约计算、依赖同步，并确保 ACID 事务性。
        """
        if instance.status != VersionStatus.WORKSPACE:
            raise ServiceException("Only workspace instances can be updated.")

        try:
            validated = WorkflowUpdate.model_validate(update_data)
        except ValidationError as e:
            raise ServiceException(f"Invalid update data: {e}")

        data_dict = validated.model_dump(exclude_unset=True)
        new_graph = data_dict.get("graph")

        # 使用嵌套事务确保图更新和引用同步的一致性
        async with self.db.begin_nested():
            if new_graph:
                # 1. 静态分析与结构校验
                try:
                    graph_obj = WorkflowGraphDef.model_validate(new_graph)
                    analyzer = WorkflowGraph(graph_obj)
                except Exception as e:
                    raise ServiceException(f"Invalid workflow graph structure: {e}")

                # 2. 提取并更新 IO 契约元数据
                self._update_contract_metadata(instance, analyzer)
                
                # 3. 更新图数据
                instance.graph = new_graph

                # 4. [Critical] 增量同步依赖引用
                # 这会修改 ai_resource_refs 表，必须在同一事务中
                await self._sync_references_incrementally(instance, analyzer, self.context.actor)

            # 更新其他字段
            for k, v in data_dict.items():
                if k != "graph" and hasattr(instance, k):
                    setattr(instance, k, v)
            
            # 显式 Flush 以确保约束检查（如外键）在事务提交前触发
            await self.db.flush()

        refreshed = await self.get_by_uuid(instance.uuid)
        if not refreshed:
            raise NotFoundError("Workflow not found after update.")
        return refreshed

    def _update_contract_metadata(self, instance: Workflow, analyzer: WorkflowGraph):
        start_node = analyzer.start_node
        end_node = analyzer.end_node
        
        # Start.outputs -> Workflow.inputs (Contract)
        # 注意：我们需要确保保存的是 dict 列表，适合 JSON 字段
        instance.inputs_schema = [p.model_dump() for p in start_node.data.outputs]
        
        # End.inputs -> Workflow.outputs (Contract)
        instance.outputs_schema = [p.model_dump() for p in end_node.data.inputs]
        
        instance.is_stream = end_node.data.config.stream

    async def delete_instance(self, instance: Workflow) -> None:
        await self.db.delete(instance)

    async def on_resource_delete(self, resource: Resource) -> None:
        pass

    async def publish_instance(self, workspace_instance: Workflow, version_tag: str, version_notes: Optional[str], actor: User) -> Workflow:
        snapshot = Workflow(
            resource_id=workspace_instance.resource_id,
            status=VersionStatus.PUBLISHED,
            version_tag=version_tag,
            version_notes=version_notes,
            creator_id=actor.id,
            published_at=func.now(),
            name=workspace_instance.name,
            description=workspace_instance.description,
            graph=workspace_instance.graph,
            inputs_schema=workspace_instance.inputs_schema,
            outputs_schema=workspace_instance.outputs_schema,
            is_stream=workspace_instance.is_stream
        )
        return snapshot

    async def serialize_instance(self, instance: Workflow) -> Dict[str, Any]:
        return WorkflowRead.model_validate(instance).model_dump()

    # ==========================================================================
    # 3. Validation & Dependencies
    # ==========================================================================

    async def validate_instance(self, instance: Workflow) -> ValidationResult:
        """
        [Semantic Validation] 执行比 DAG 更深层的检查。
        """
        errors = []
        try:
            # 1. 结构检查
            graph_obj = WorkflowGraphDef.model_validate(instance.graph)
            analyzer = WorkflowGraph(graph_obj)
            
            # 2. 引用完整性检查
            # 检查所有在 DB 中记录的引用是否仍然指向有效的 PUBLISHED 资源
            refs = await self.ref_dao.get_dependencies(instance.id)
            for ref in refs:
                if not ref.target_instance:
                    errors.append(f"Node {ref.source_node_uuid}: Referenced resource no longer exists.")
                elif ref.target_instance.status != VersionStatus.PUBLISHED:
                    errors.append(f"Node {ref.source_node_uuid}: Referenced resource '{ref.target_instance.name}' is not published.")

            # 3. 参数引用与 Loop 语义校验
            errors.extend(self._validate_parameter_references(analyzer))
            
        except Exception as e:
            errors.append(f"Graph validation failed: {str(e)}")

        return ValidationResult(is_valid=not errors, errors=errors)

    def _validate_parameter_references(self, analyzer: WorkflowGraph) -> List[str]:
        errors: List[str] = []
        node_map = {node.id: node for node in analyzer.all_nodes}
        ancestors = self._build_ancestor_map(analyzer)

        for node in analyzer.all_nodes:
            # A. 常规 inputs / outputs 的 ref 校验
            schema_entries: List[Tuple[str, List[ParameterSchema]]] = [
                ("inputs", node.data.inputs or []),
                ("outputs", node.data.outputs or []),
            ]
            for section, schema_list in schema_entries:
                for schema_path, ref in self._collect_schema_refs(schema_list, prefix=section):
                    # loop-block-output 仅允许在 Loop 节点 outputs 中出现
                    if ref.get("source") == "loop-block-output":
                        if node.data.registryId != "Loop" or section != "outputs":
                            errors.append(
                                f"Node {node.id}: {schema_path} uses source=loop-block-output outside Loop outputs."
                            )
                        continue
                    errors.extend(
                        self._validate_ref_target(
                            node_id=node.id,
                            ref=ref,
                            node_map=node_map,
                            ancestors=ancestors,
                            schema_path=schema_path,
                        )
                    )

            # B. 特殊配置字段中的 ParameterSchema 引用
            cfg = node.data.config
            raw_loop_count = getattr(cfg, "loopCount", None)
            loop_count_schema = self._as_parameter_schema(raw_loop_count)
            if loop_count_schema:
                errors.extend(
                    self._validate_refs_for_single_schema(
                        node.id,
                        loop_count_schema,
                        "config.loopCount",
                        node_map,
                        ancestors,
                    )
                )
            raw_loop_list = getattr(cfg, "loopList", None)
            loop_list_schema = self._as_parameter_schema(raw_loop_list)
            if loop_list_schema:
                errors.extend(
                    self._validate_refs_for_single_schema(
                        node.id,
                        loop_list_schema,
                        "config.loopList",
                        node_map,
                        ancestors,
                    )
                )
            if hasattr(cfg, "branchs"):
                for idx, branch in enumerate(getattr(cfg, "branchs") or []):
                    conditions = branch.get("conditions", []) if isinstance(branch, dict) else getattr(branch, "conditions", []) or []
                    for cond_idx, cond in enumerate(conditions):
                        left = cond.get("left") if isinstance(cond, dict) else getattr(cond, "left", None)
                        right = cond.get("right") if isinstance(cond, dict) else getattr(cond, "right", None)
                        left_schema = self._as_parameter_schema(left)
                        right_schema = self._as_parameter_schema(right)
                        if left_schema:
                            errors.extend(
                                self._validate_refs_for_single_schema(
                                    node.id,
                                    left_schema,
                                    f"config.branchs[{idx}].conditions[{cond_idx}].left",
                                    node_map,
                                    ancestors,
                                )
                            )
                        if right_schema:
                            errors.extend(
                                self._validate_refs_for_single_schema(
                                    node.id,
                                    right_schema,
                                    f"config.branchs[{idx}].conditions[{cond_idx}].right",
                                    node_map,
                                    ancestors,
                                )
                            )

            # C. Loop 节点的专项校验（内层 blocks + loop 输出引用）
            if node.data.registryId == "Loop":
                errors.extend(self._validate_loop_node(node))

        return errors

    def _validate_refs_for_single_schema(
        self,
        node_id: str,
        schema: ParameterSchema,
        schema_path: str,
        node_map: Dict[str, Any],
        ancestors: Dict[str, Set[str]],
    ) -> List[str]:
        errors: List[str] = []
        parsed_schema = self._as_parameter_schema(schema)
        if not parsed_schema:
            return [f"Node {node_id}: {schema_path} is not a valid ParameterSchema."]
        ref_entries = self._collect_schema_refs([parsed_schema], prefix=schema_path)
        for path, ref in ref_entries:
            if ref.get("source") == "loop-block-output":
                errors.append(f"Node {node_id}: {path} uses source=loop-block-output in unsupported context.")
                continue
            errors.extend(
                self._validate_ref_target(
                    node_id=node_id,
                    ref=ref,
                    node_map=node_map,
                    ancestors=ancestors,
                    schema_path=path,
                )
            )
        return errors

    def _validate_ref_target(
        self,
        node_id: str,
        ref: Dict[str, str],
        node_map: Dict[str, Any],
        ancestors: Dict[str, Set[str]],
        schema_path: str,
    ) -> List[str]:
        errors: List[str] = []
        block_id = ref.get("blockID", "").strip()
        ref_path = ref.get("path", "").strip()

        if not block_id:
            return [f"Node {node_id}: {schema_path} has empty ref.blockID."]
        if not ref_path:
            return [f"Node {node_id}: {schema_path} has empty ref.path."]
        if block_id not in node_map:
            return [f"Node {node_id}: {schema_path} references unknown node '{block_id}'."]
        if block_id not in ancestors.get(node_id, set()):
            return [f"Node {node_id}: {schema_path} references non-upstream node '{block_id}'."]

        source_node = node_map[block_id]
        source_schemas = source_node.data.outputs or []
        if not self._path_exists_in_schemas(source_schemas, ref_path):
            errors.append(
                f"Node {node_id}: {schema_path} path '{ref_path}' not found in node '{block_id}' outputs."
            )
        return errors

    def _validate_loop_node(self, loop_node: Any) -> List[str]:
        errors: List[str] = []
        config = loop_node.data.config
        loop_type = getattr(config, "loopType", "count")
        loop_count = self._as_parameter_schema(getattr(config, "loopCount", None))
        loop_list = self._as_parameter_schema(getattr(config, "loopList", None))

        # A. Loop 核心配置校验
        if loop_type == "count":
            if not loop_count:
                errors.append(f"Node {loop_node.id}: loopType=count requires config.loopCount.")
            elif loop_count.type not in ("integer", "number"):
                errors.append(f"Node {loop_node.id}: config.loopCount type must be integer or number.")
        elif loop_type == "list":
            if not loop_list:
                errors.append(f"Node {loop_node.id}: loopType=list requires config.loopList.")
            elif loop_list.type != "array":
                errors.append(f"Node {loop_node.id}: config.loopList type must be array.")
        else:
            errors.append(f"Node {loop_node.id}: Unsupported loopType '{loop_type}'.")

        # B. Loop 内部子节点结构
        blocks = list(loop_node.data.blocks or [])
        block_map = {b.id: b for b in blocks}
        block_ancestors = self._build_internal_ancestor_map(blocks, loop_node.data.edges or [])

        # C. 校验 loop outputs 中 source=loop-block-output 的引用
        for schema_path, ref in self._collect_schema_refs(loop_node.data.outputs or [], "outputs"):
            if ref.get("source") != "loop-block-output":
                continue
            ref_block = ref.get("blockID", "").strip()
            ref_path = ref.get("path", "").strip()
            if not ref_block:
                errors.append(f"Node {loop_node.id}: {schema_path} has empty loop-block-output.blockID.")
                continue
            if ref_block not in block_map:
                errors.append(
                    f"Node {loop_node.id}: {schema_path} references unknown loop block '{ref_block}'."
                )
                continue
            if not ref_path:
                errors.append(f"Node {loop_node.id}: {schema_path} has empty loop-block-output.path.")
                continue
            if not self._path_exists_in_schemas(block_map[ref_block].data.outputs or [], ref_path):
                errors.append(
                    f"Node {loop_node.id}: {schema_path} path '{ref_path}' not found in loop block '{ref_block}' outputs."
                )

        # D. 校验 loop blocks 中的引用
        loop_input_schemas = loop_node.data.inputs or []
        for block in blocks:
            for schema_path, ref in self._collect_schema_refs(block.data.inputs or [], "inputs"):
                ref_block = ref.get("blockID", "").strip()
                ref_path = ref.get("path", "").strip()
                if not ref_block:
                    errors.append(f"Node {block.id}: {schema_path} has empty ref.blockID.")
                    continue
                if not ref_path:
                    errors.append(f"Node {block.id}: {schema_path} has empty ref.path.")
                    continue
                if ref.get("source") == "loop-block-output":
                    errors.append(
                        f"Node {block.id}: {schema_path} cannot use source=loop-block-output inside loop blocks."
                    )
                    continue

                if ref_block == loop_node.id:
                    if ref_path in {"index", "item"}:
                        continue
                    if not self._path_exists_in_schemas(loop_input_schemas, ref_path):
                        errors.append(
                            f"Node {block.id}: {schema_path} references Loop.{ref_path}, but it is neither index/item nor a loop input."
                        )
                    continue

                if ref_block not in block_map:
                    errors.append(
                        f"Node {block.id}: {schema_path} references unknown loop block '{ref_block}'."
                    )
                    continue

                if ref_block not in block_ancestors.get(block.id, set()):
                    errors.append(
                        f"Node {block.id}: {schema_path} references non-upstream loop block '{ref_block}'."
                    )
                    continue

                source_schemas = block_map[ref_block].data.outputs or []
                if not self._path_exists_in_schemas(source_schemas, ref_path):
                    errors.append(
                        f"Node {block.id}: {schema_path} path '{ref_path}' not found in loop block '{ref_block}' outputs."
                    )

        return errors

    def _build_ancestor_map(self, analyzer: WorkflowGraph) -> Dict[str, Set[str]]:
        memo: Dict[str, Set[str]] = {}

        def collect(node_id: str) -> Set[str]:
            if node_id in memo:
                return memo[node_id]
            parents = set(analyzer.get_predecessors(node_id))
            all_parents = set(parents)
            for parent_id in parents:
                all_parents.update(collect(parent_id))
            memo[node_id] = all_parents
            return all_parents

        for node in analyzer.all_nodes:
            collect(node.id)
        return memo

    def _build_internal_ancestor_map(
        self,
        blocks: List[Any],
        edges: List[Any],
    ) -> Dict[str, Set[str]]:
        block_ids = {b.id for b in blocks}
        predecessors: Dict[str, Set[str]] = {node_id: set() for node_id in block_ids}
        for edge in edges:
            source_id = getattr(edge, "sourceNodeID", "")
            target_id = getattr(edge, "targetNodeID", "")
            if source_id in block_ids and target_id in block_ids:
                predecessors[target_id].add(source_id)

        memo: Dict[str, Set[str]] = {}

        def collect(node_id: str) -> Set[str]:
            if node_id in memo:
                return memo[node_id]
            direct = predecessors.get(node_id, set())
            all_parents = set(direct)
            for parent_id in direct:
                all_parents.update(collect(parent_id))
            memo[node_id] = all_parents
            return all_parents

        for node_id in block_ids:
            collect(node_id)
        return memo

    def _collect_schema_refs(
        self,
        schemas: List[ParameterSchema],
        prefix: str = "",
    ) -> List[Tuple[str, Dict[str, str]]]:
        refs: List[Tuple[str, Dict[str, str]]] = []
        for schema in schemas or []:
            name = getattr(schema, "name", None)
            if not name:
                continue
            current_path = f"{prefix}.{name}" if prefix else name
            value = getattr(schema, "value", None)
            if value and getattr(value, "type", None) == "ref":
                content = getattr(value, "content", None)
                content_dict = self._to_ref_dict(content)
                if content_dict:
                    refs.append((current_path, content_dict))
            child_props = getattr(schema, "properties", None) or []
            if child_props:
                refs.extend(self._collect_schema_refs(child_props, current_path))
            items = getattr(schema, "items", None)
            item_props = getattr(items, "properties", None) if items else None
            if item_props:
                refs.extend(self._collect_schema_refs(item_props, current_path))
        return refs

    def _to_ref_dict(self, content: Any) -> Optional[Dict[str, str]]:
        if content is None:
            return None
        if isinstance(content, dict):
            return {
                "blockID": str(content.get("blockID", "")),
                "path": str(content.get("path", "")),
                "source": str(content.get("source", "")) if content.get("source") else "",
            }
        block_id = getattr(content, "blockID", None)
        path = getattr(content, "path", None)
        source = getattr(content, "source", None)
        if block_id is None and path is None:
            return None
        return {
            "blockID": str(block_id or ""),
            "path": str(path or ""),
            "source": str(source or ""),
        }

    def _as_parameter_schema(self, value: Any) -> Optional[ParameterSchema]:
        if value is None:
            return None
        if isinstance(value, ParameterSchema):
            return value
        if isinstance(value, dict):
            try:
                return ParameterSchema.model_validate(value)
            except Exception:
                return None
        return None

    def _path_exists_in_schemas(self, schemas: List[ParameterSchema], path: str) -> bool:
        parts = [part for part in (path or "").split(".") if part]
        if not parts:
            return False
        return self._path_exists_in_schema_parts(schemas or [], parts)

    def _path_exists_in_schema_parts(self, schemas: List[ParameterSchema], parts: List[str]) -> bool:
        if not parts:
            return True
        current_name = parts[0]
        current = next((schema for schema in schemas if getattr(schema, "name", None) == current_name), None)
        if not current:
            return False
        if len(parts) == 1:
            return True
        if current.type == "object":
            return self._path_exists_in_schema_parts(current.properties or [], parts[1:])
        if current.type == "array":
            items = current.items
            if not items:
                return False
            if items.type == "object":
                return self._path_exists_in_schema_parts(items.properties or [], parts[1:])
            return False
        return False

    async def get_dependencies(self, instance: Workflow) -> List[DependencyInfo]:
        refs = await self.ref_dao.get_dependencies(instance.id)
        return [
            DependencyInfo(
                resource_uuid=ref.target_resource.uuid,
                instance_uuid=ref.target_instance.uuid,
                alias=ref.alias
            ) for ref in refs
        ]

    # ==========================================================================
    # 4. Execution Core (The Unified Generator)
    # ==========================================================================

    async def execute(
        self, 
        instance_uuid: str, 
        execute_params: WorkflowExecutionRequest, 
        actor: User, 
        runtime_workspace: Optional[Workspace] = None
    ) -> WorkflowExecutionResponse:
        final_output = None
        trace_id = None
        run_id = None
        thread_id = None
        interrupt_payload = None
        outcome = "success"
        task: Optional[asyncio.Task] = None

        try:
            result = await self.async_execute(
                instance_uuid,
                execute_params,
                actor,
                runtime_workspace,
            ) 
            task = result.task
            run_id = result.run_id
            thread_id = result.thread_id
            trace_id = result.trace_id
            async for event in result.generator:
                if event.event == "run.started":
                    trace_id = event.data.get("trace_id") or trace_id
                    run_id = event.data.get("run_id") or run_id
                    thread_id = event.data.get("thread_id") or thread_id
                elif event.event == "run.finished":
                    final_output = event.data.get("output")
                    outcome = event.data.get("outcome") or outcome
                elif event.event == "run.failed":
                    error_msg = event.data.get("error") if isinstance(event.data, dict) else str(event.data)
                    raise ServiceException(f"Workflow execution failed: {error_msg}")
                elif event.event == "run.interrupted":
                    interrupt_payload = event.data.get("interrupt") if isinstance(event.data, dict) else None
                    outcome = "interrupt"
                elif event.event == "run.cancelled":
                    outcome = "cancelled"
        except Exception as exc:
            raise ServiceException(f"Workflow failed: {exc}")
        finally:
            if task and not task.done():
                try:
                    await task
                except Exception:
                    pass

        if final_output is None and interrupt_payload is None:
            raise ServiceException("Workflow finished without output.")

        return WorkflowExecutionResponse(
            data=WorkflowExecutionResponseData(
                output=final_output or {},
                trace_id=trace_id or "",
                run_id=run_id,
                thread_id=thread_id,
                outcome=outcome,
                interrupt=WorkflowInterruptRead.model_validate(interrupt_payload) if interrupt_payload else None,
            )
        )

    async def execute_batch(
        self,
        instance_uuids: List[str],
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> List[WorkflowExecutionResponse]:
        results = []
        for uuid in instance_uuids:
            result = await self.execute(uuid, execute_params, actor, runtime_workspace)
            results.append(result)
        return results

    async def async_execute(
        self, 
        instance_uuid: str, 
        execute_params: WorkflowExecutionRequest, 
        actor: User, 
        runtime_workspace: Optional[Workspace] = None
    ) -> WorkflowRunResult:
        runner = WorkflowRuntimeRunner(
            base_context=self.context,
            db_session_factory=self._db_session_factory,
        )
        return await runner.start(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

    async def start_background_execute(
        self,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> WorkflowRunSummaryRead:
        prepared = await self._prepare_run_context(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

        try:
            await self.context.arq_pool.enqueue_job(
                "execute_workflow_run_task",
                run_id=prepared["execution"].run_id,
                instance_uuid=instance_uuid,
                actor_uuid=actor.uuid,
                execute_params=execute_params.model_dump(mode="json", by_alias=True, exclude_none=False),
            )
        except Exception as exc:
            await self.execution_ledger_service.mark_finished(
                prepared["execution"],
                status=ResourceExecutionStatus.FAILED,
                error_code="WORKFLOW_ENQUEUE_ERROR",
                error_message=str(exc),
            )
            await self.db.commit()
            raise ServiceException(f"Failed to enqueue workflow run: {exc}")

        return self._to_run_summary(prepared["execution"], latest_checkpoint=None)

    async def _prepare_async_run(
        self,
        *,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> PreparedWorkflowRun:
        generator_manager = AsyncGeneratorManager()

        try:
            prepared = await self._prepare_run_context(
                instance_uuid=instance_uuid,
                execute_params=execute_params,
                actor=actor,
                runtime_workspace=runtime_workspace,
            )

            callbacks = WorkflowStreamCallbacks(
                generator_manager=generator_manager,
                trace_id=prepared["trace_id"],
                run_id=prepared["execution"].run_id,
                thread_id=prepared["execution"].thread_id,
                event_persister=self._build_event_persister(
                    execution=prepared["execution"],
                    workflow_instance=prepared["workflow_instance"],
                ),
            )
            live_event_buffer = self.live_event_service.create_buffer(prepared["execution"].run_id)
            callbacks.bind_live_event_buffer(live_event_buffer)

            return PreparedWorkflowRun(
                result=WorkflowRunResult(
                    generator=generator_manager,
                    trace_id=prepared["trace_id"],
                    run_id=prepared["execution"].run_id,
                    thread_id=prepared["execution"].thread_id,
                    detach=live_event_buffer.detach,
                ),
                background_task_kwargs={
                    "execution": prepared["execution"],
                    "workflow_instance": prepared["workflow_instance"],
                    "runtime_plan": prepared["runtime_plan"],
                    "restored_snapshot": prepared["restored_snapshot"],
                    "payload": prepared["payload"],
                    "callbacks": callbacks,
                    "generator_manager": generator_manager,
                    "external_context": prepared["external_context"],
                    "trace_id": prepared["trace_id"],
                    "actor": actor,
                    "live_event_buffer": live_event_buffer,
                },
            )
        except Exception:
            await generator_manager.aclose(force=True)
            execution = locals().get("prepared", {}).get("execution") if isinstance(locals().get("prepared"), dict) else None
            if execution is not None:
                await self.execution_ledger_service.mark_finished(
                    execution,
                    status=ResourceExecutionStatus.FAILED,
                    error_code="WORKFLOW_RUN_INIT_ERROR",
                    error_message="Workflow runtime initialization failed.",
                )
                await self.db.commit()
            raise

    async def _run_workflow_background_task(
        self,
        *,
        execution,
        workflow_instance: Workflow,
        runtime_plan: WorkflowRuntimePlan,
        restored_snapshot: Optional[WorkflowRuntimeSnapshot],
        payload: Dict[str, Any],
        callbacks: WorkflowStreamCallbacks,
        generator_manager: AsyncGeneratorManager,
        external_context: ExternalContext,
        trace_id: str,
        actor: User,
        live_event_buffer=None,
    ) -> None:
        tracing_interceptor = WorkflowTraceInterceptor(
            db=self.db,
            user_id=actor.id,
            workflow_trace_id=trace_id,
        )
        runtime_observer = WorkflowDurableRuntimeObserver(
            context=self.context,
            execution=execution,
            workflow_instance=workflow_instance,
            runtime_plan=runtime_plan,
            event_callback=callbacks.on_event,
        )

        try:
            await self.execution_ledger_service.mark_running(execution, trace_id=trace_id)
            await self.db.commit()

            async with TraceManager(
                db=self.db,
                operation_name="workflow.run",
                user_id=actor.id,
                force_trace_id=trace_id,
                target_instance_id=workflow_instance.id,
                attributes=WorkflowAttributes(inputs=payload),
            ) as root_span:
                final_output = await self.engine_service.run(
                    workflow_def=runtime_plan,
                    payload=payload,
                    callbacks=callbacks,
                    external_context=external_context,
                    interceptors=[tracing_interceptor],
                    restored_snapshot=restored_snapshot,
                    runtime_observer=runtime_observer,
                )
                root_span.set_output(final_output)

            await self.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.SUCCEEDED,
            )
            await self.db.commit()
        except WorkflowInterruptSignal as interrupt_exc:
            interrupt_payload = interrupt_exc.interrupt.model_dump(mode="json")
            await callbacks.on_event(
                "run.interrupted",
                {
                    "interrupt": interrupt_payload,
                    "outcome": "interrupt",
                    "run_id": execution.run_id,
                    "thread_id": execution.thread_id,
                },
            )
            await self.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.INTERRUPTED,
            )
            await self.db.commit()
        except asyncio.CancelledError:
            logger.info("Workflow %s execution cancelled.", workflow_instance.uuid)
            await runtime_observer.request_cancel()
            await self.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.CANCELLED,
                error_code="WORKFLOW_CANCELLED",
                error_message="Operation cancelled.",
            )
            await self.db.commit()
            await callbacks.on_event(
                "run.cancelled",
                {
                    "output": {},
                    "outcome": "cancelled",
                    "run_id": execution.run_id,
                    "thread_id": execution.thread_id,
                },
            )
            raise
        except Exception as exc:
            logger.error("Workflow execution error: %s", exc, exc_info=True)
            await callbacks.on_event("run.failed", {"error": str(exc)})
            status = ResourceExecutionStatus.CANCELLED if await runtime_observer.should_cancel() else ResourceExecutionStatus.FAILED
            error_code = "WORKFLOW_CANCELLED" if status == ResourceExecutionStatus.CANCELLED else "WORKFLOW_EXECUTION_ERROR"
            await self.execution_ledger_service.mark_finished(
                execution,
                status=status,
                error_code=error_code,
                error_message=str(exc),
            )
            await self.db.commit()
        finally:
            WorkflowTaskRegistry.unregister(execution.run_id)
            if live_event_buffer is not None:
                await live_event_buffer.aclose()
            await generator_manager.aclose(force=False)

    async def execute_precreated_run(
        self,
        *,
        run_id: str,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> None:
        prepared = await self._prepare_run_context(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
            existing_run_id=run_id,
        )
        generator_manager = AsyncGeneratorManager()
        callbacks = WorkflowStreamCallbacks(
            generator_manager=generator_manager,
            trace_id=prepared["trace_id"],
            run_id=prepared["execution"].run_id,
            thread_id=prepared["execution"].thread_id,
            event_persister=self._build_event_persister(
                execution=prepared["execution"],
                workflow_instance=prepared["workflow_instance"],
            ),
        )
        live_event_buffer = self.live_event_service.create_buffer(prepared["execution"].run_id)
        live_event_buffer.detach()
        callbacks.bind_live_event_buffer(live_event_buffer)
        await self._run_workflow_background_task(
            execution=prepared["execution"],
            workflow_instance=prepared["workflow_instance"],
            runtime_plan=prepared["runtime_plan"],
            restored_snapshot=prepared["restored_snapshot"],
            payload=prepared["payload"],
            callbacks=callbacks,
            generator_manager=generator_manager,
            external_context=prepared["external_context"],
            trace_id=prepared["trace_id"],
            actor=actor,
            live_event_buffer=live_event_buffer,
        )

    async def _prepare_run_context(
        self,
        *,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
        existing_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        instance = await self.get_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Workflow not found")
        await self._check_execute_perm(instance)

        workspace = runtime_workspace or instance.resource.workspace
        trace_id = str(uuid.uuid4())
        graph_override = None
        if isinstance(execute_params.meta, dict):
            graph_override = execute_params.meta.get("_workflow_graph_override")
        runtime_plan = self.runtime_compiler.compile(graph_override or instance.graph)
        restored_snapshot: Optional[WorkflowRuntimeSnapshot] = None
        payload = dict(execute_params.inputs or {})
        resume_payload = None
        requested_thread_id = (execute_params.thread_id or "").strip()
        parent_run_id = (execute_params.parent_run_id or "").strip() or None
        resume_from_run_id = (execute_params.resume_from_run_id or "").strip() or None

        parent_execution = None
        if resume_from_run_id:
            if payload:
                raise ServiceException("Resume execution does not accept new inputs.")
            parent_execution = await self.execution_ledger_service.get_by_run_id(resume_from_run_id)
            if not parent_execution:
                raise NotFoundError("Resume target run not found.")
            if parent_execution.resource_instance_id != instance.id or parent_execution.user_id != actor.id:
                raise ServiceException("Resume target does not belong to this workflow or actor.")
            if parent_execution.status == ResourceExecutionStatus.RUNNING:
                raise ServiceException("Cannot resume a workflow that is still running.")
            resume_payload = await self._resolve_resume_payload(
                parent_execution=parent_execution,
                execute_params=execute_params,
            )

            checkpoint = await self.runtime_persistence.get_latest_checkpoint(execution_id=parent_execution.id)
            if checkpoint is None:
                raise ServiceException("No checkpoint available for resume.")

            runtime_plan = WorkflowRuntimePlan.model_validate(checkpoint.runtime_plan)
            restored_snapshot = self.runtime_persistence.build_resume_snapshot(
                checkpoint=checkpoint,
                runtime_plan=runtime_plan,
            )
            payload = dict(restored_snapshot.payload or {})
            requested_thread_id = parent_execution.thread_id
            parent_run_id = parent_execution.run_id

        elif parent_run_id:
            parent_execution = await self.execution_ledger_service.get_by_run_id(parent_run_id)
            if not parent_execution:
                raise NotFoundError("Parent run not found.")
            if parent_execution.resource_instance_id != instance.id or parent_execution.user_id != actor.id:
                raise ServiceException("Parent run does not belong to this workflow or actor.")
            requested_thread_id = requested_thread_id or parent_execution.thread_id
            if requested_thread_id != parent_execution.thread_id:
                raise ServiceException("Parent run thread mismatch.")

        execution = None
        if existing_run_id:
            execution = await self.execution_ledger_service.get_by_run_id(existing_run_id)
            if execution is None:
                raise NotFoundError("Workflow run not found.")
            if execution.resource_instance_id != instance.id or execution.user_id != actor.id:
                raise ServiceException("Workflow run does not belong to this workflow or actor.")
            requested_thread_id = execution.thread_id
        else:
            thread_id = requested_thread_id or f"workflow-thread-{uuid.uuid4().hex[:16]}"
            execution = await self.execution_ledger_service.create_execution(
                instance=instance,
                actor=actor,
                thread_id=thread_id,
                parent_run_id=parent_run_id,
            )
            await self.db.commit()

        external_context = ExternalContext(
            app_context=self.context,
            workflow_instance=instance,
            runtime_workspace=workspace,
            trace_id=trace_id,
            run_id=execution.run_id,
            thread_id=execution.thread_id,
            resume_payload=resume_payload,
        )
        return {
            "execution": execution,
            "workflow_instance": instance,
            "runtime_plan": runtime_plan,
            "restored_snapshot": restored_snapshot,
            "payload": payload,
            "external_context": external_context,
            "trace_id": trace_id,
        }

    def _build_event_persister(self, *, execution, workflow_instance: Workflow):
        async def _persist(event_type: str, payload: Dict[str, Any]) -> None:
            try:
                await self.event_log_service.append_event(
                    execution=execution,
                    workflow_instance=workflow_instance,
                    event_type=event_type,
                    payload=payload,
                )
                await self.db.commit()
            except Exception:
                logger.exception("Failed to persist workflow event %s for run %s", event_type, execution.run_id)

        return _persist

    def _to_run_summary(self, execution, latest_checkpoint) -> WorkflowRunSummaryRead:
        status_value = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        return WorkflowRunSummaryRead(
            run_id=execution.run_id,
            thread_id=execution.thread_id,
            parent_run_id=execution.parent_run_id,
            status=status_value,
            trace_id=execution.trace_id,
            error_code=execution.error_code,
            error_message=execution.error_message,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            latest_checkpoint=self.runtime_persistence.build_checkpoint_read(execution=execution, checkpoint=latest_checkpoint) if latest_checkpoint else None,
        )

    async def _get_latest_interrupt(
        self,
        *,
        execution_id: int,
    ) -> Optional[WorkflowInterruptRead]:
        latest_interrupt = await self.event_log_service.get_latest_event(
            execution_id=execution_id,
            event_type="run.interrupted",
        )
        if latest_interrupt is None:
            latest_interrupt = await self.event_log_service.get_latest_event(
                execution_id=execution_id,
                event_type="interrupt",
            )
        if latest_interrupt is None or not isinstance(latest_interrupt.payload, dict):
            return None

        interrupt_payload = latest_interrupt.payload.get("interrupt")
        if not isinstance(interrupt_payload, dict):
            return None

        try:
            return WorkflowInterruptRead.model_validate(interrupt_payload)
        except ValidationError:
            logger.warning("Invalid persisted workflow interrupt payload for execution %s", execution_id, exc_info=True)
            return None

    async def _resolve_resume_payload(
        self,
        *,
        parent_execution,
        execute_params: WorkflowExecutionRequest,
    ) -> Any:
        if execute_params.resume is None:
            return execute_params.meta.get("resume") if isinstance(execute_params.meta, dict) else None

        resume_token = execute_params.resume.token
        if resume_token is not None:
            if resume_token.run_id != parent_execution.run_id:
                raise ServiceException("Resume token run mismatch.")
            if resume_token.thread_id != parent_execution.thread_id:
                raise ServiceException("Resume token thread mismatch.")

        resume_payload = execute_params.resume.output
        interrupt = await self._get_latest_interrupt(execution_id=parent_execution.id)
        resume_key = None
        if interrupt is not None and isinstance(interrupt.payload, dict):
            payload_resume_key = interrupt.payload.get("resumeOutputKey")
            if isinstance(payload_resume_key, str) and payload_resume_key.strip():
                resume_key = payload_resume_key.strip()
            interrupt_token = interrupt.resume_token
            if resume_token is not None and interrupt_token is not None and interrupt_token.node_id != resume_token.node_id:
                raise ServiceException("Resume token node mismatch.")

        if resume_key:
            return {resume_key: resume_payload}
        return resume_payload

    async def get_run(self, run_id: str) -> WorkflowRunRead:
        execution = await self.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Workflow run not found.")

        workflow_stub = await self.dao.get_by_pk(execution.resource_instance_id)
        if workflow_stub is None:
            raise NotFoundError("Workflow instance not found.")
        workflow_instance = await self.get_by_uuid(workflow_stub.uuid)
        if workflow_instance is None:
            raise NotFoundError("Workflow instance not found.")

        await self._check_execute_perm(workflow_instance)

        latest_checkpoint = await self.runtime_persistence.get_latest_checkpoint(execution_id=execution.id)
        node_executions = await self.runtime_persistence.node_execution_dao.get_list(
            where={"resource_execution_id": execution.id},
            order=["id"],
        )
        interrupt = await self._get_latest_interrupt(execution_id=execution.id)

        status_value = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        can_resume = status_value in {
            ResourceExecutionStatus.FAILED.value,
            ResourceExecutionStatus.CANCELLED.value,
            ResourceExecutionStatus.INTERRUPTED.value,
        } and latest_checkpoint is not None

        return WorkflowRunRead(
            run_id=execution.run_id,
            thread_id=execution.thread_id,
            parent_run_id=execution.parent_run_id,
            status=status_value,
            trace_id=execution.trace_id,
            error_code=execution.error_code,
            error_message=execution.error_message,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            workflow_instance_uuid=workflow_instance.uuid,
            workflow_name=workflow_instance.name,
            latest_checkpoint=self.runtime_persistence.build_checkpoint_read(execution=execution, checkpoint=latest_checkpoint) if latest_checkpoint else None,
            node_executions=[WorkflowRunNodeRead.model_validate(item) for item in node_executions],
            can_resume=can_resume,
            interrupt=interrupt,
        )

    async def list_run_events(
        self,
        run_id: str,
        *,
        limit: int = 1000,
    ) -> List[WorkflowEventRead]:
        execution = await self.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Workflow run not found.")

        workflow_stub = await self.dao.get_by_pk(execution.resource_instance_id)
        if workflow_stub is None:
            raise NotFoundError("Workflow instance not found.")
        workflow_instance = await self.get_by_uuid(workflow_stub.uuid)
        if workflow_instance is None:
            raise NotFoundError("Workflow instance not found.")
        await self._check_execute_perm(workflow_instance)

        return await self.event_log_service.list_events(
            execution_id=execution.id,
            limit=limit,
        )

    async def stream_live_run_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
    ):
        execution = await self.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Workflow run not found.")

        workflow_stub = await self.dao.get_by_pk(execution.resource_instance_id)
        if workflow_stub is None:
            raise NotFoundError("Workflow instance not found.")
        workflow_instance = await self.get_by_uuid(workflow_stub.uuid)
        if workflow_instance is None:
            raise NotFoundError("Workflow instance not found.")
        await self._check_execute_perm(workflow_instance)

        current_seq = after_seq
        seen_terminal_event = False
        async for envelope in self.live_event_service.stream_events(run_id, after_seq=after_seq):
            try:
                current_seq = max(current_seq, int(envelope.get("seq", current_seq)))
            except Exception:
                pass
            payload = envelope.get("payload", {})
            if isinstance(payload, dict) and str(payload.get("event", "")) in {"run.finished", "run.failed", "run.interrupted", "run.cancelled", "system.error"}:
                seen_terminal_event = True
            yield envelope

        for event in await self.event_log_service.list_events_after_sequence(
            execution_id=execution.id,
            after_sequence_no=current_seq,
            limit=1000,
        ):
            if event.event_type in {"run.finished", "run.failed", "run.interrupted", "run.cancelled", "system.error"}:
                seen_terminal_event = True
            yield {
                "seq": event.sequence_no,
                "payload": {
                    "event": event.event_type,
                    "data": event.payload,
                },
            }

        if not seen_terminal_event:
            await self.db.refresh(execution)
            terminal_event_type = {
                ResourceExecutionStatus.SUCCEEDED: "run.finished",
                ResourceExecutionStatus.INTERRUPTED: "run.interrupted",
                ResourceExecutionStatus.CANCELLED: "run.cancelled",
                ResourceExecutionStatus.FAILED: "run.failed",
            }.get(execution.status)
            if terminal_event_type is not None:
                terminal_event = await self.event_log_service.get_latest_event(
                    execution_id=execution.id,
                    event_type=terminal_event_type,
                )
                if terminal_event is not None and terminal_event.sequence_no >= after_seq:
                    yield {
                        "seq": terminal_event.sequence_no,
                        "payload": {
                            "event": terminal_event.event_type,
                            "data": terminal_event.payload,
                        },
                    }

    async def list_runs(
        self,
        instance_uuid: str,
        *,
        limit: int = 20,
    ) -> List[WorkflowRunSummaryRead]:
        instance = await self.get_by_uuid(instance_uuid)
        if instance is None:
            raise NotFoundError("Workflow not found.")
        await self._check_execute_perm(instance)

        rows = await self.execution_ledger_service.dao.get_list(
            where={"resource_instance_id": instance.id},
            order=[
                self.execution_ledger_service.dao.model.created_at.desc(),
                self.execution_ledger_service.dao.model.id.desc(),
            ],
            limit=limit,
        )

        summaries: List[WorkflowRunSummaryRead] = []
        for execution in rows:
            latest_checkpoint = await self.runtime_persistence.get_latest_checkpoint(execution_id=execution.id)
            status_value = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
            summaries.append(
                WorkflowRunSummaryRead(
                    run_id=execution.run_id,
                    thread_id=execution.thread_id,
                    parent_run_id=execution.parent_run_id,
                    status=status_value,
                    trace_id=execution.trace_id,
                    error_code=execution.error_code,
                    error_message=execution.error_message,
                    started_at=execution.started_at,
                    finished_at=execution.finished_at,
                    latest_checkpoint=self.runtime_persistence.build_checkpoint_read(execution=execution, checkpoint=latest_checkpoint) if latest_checkpoint else None,
                )
            )
        return summaries

    async def debug_node_execute(
        self,
        instance_uuid: str,
        node_id: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> WorkflowExecutionResponse:
        debug_request = await self.build_debug_node_request(
            instance_uuid=instance_uuid,
            node_id=node_id,
            execute_params=execute_params,
        )
        return await self.execute(
            instance_uuid=instance_uuid,
            execute_params=debug_request,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

    async def build_debug_node_request(
        self,
        *,
        instance_uuid: str,
        node_id: str,
        execute_params: WorkflowExecutionRequest,
    ) -> WorkflowExecutionRequest:
        instance = await self.get_by_uuid(instance_uuid)
        if instance is None:
            raise NotFoundError("Workflow not found.")
        await self._check_execute_perm(instance)

        graph_obj = WorkflowGraphDef.model_validate(instance.graph)
        debug_graph = self._build_node_debug_graph(graph_obj, node_id)
        meta = dict(execute_params.meta or {})
        meta["_workflow_graph_override"] = debug_graph
        debug_request = WorkflowExecutionRequest(
            inputs=execute_params.inputs,
            meta=meta,
            thread_id=execute_params.thread_id,
            parent_run_id=execute_params.parent_run_id,
            resume_from_run_id=execute_params.resume_from_run_id,
            resume=execute_params.resume,
        )
        return debug_request

    async def cancel_run(self, run_id: str) -> Dict[str, Any]:
        execution = await self.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Workflow run not found.")

        workflow_stub = await self.dao.get_by_pk(execution.resource_instance_id)
        if workflow_stub is None:
            raise NotFoundError("Workflow instance not found.")
        workflow_instance = await self.get_by_uuid(workflow_stub.uuid)
        if workflow_instance is None:
            raise NotFoundError("Workflow instance not found.")

        await self._check_execute_perm(workflow_instance)

        await self.context.redis_service.set_json(
            WorkflowDurableRuntimeObserver.cancel_signal_key(run_id),
            {"requested_at": datetime.now(UTC).replace(tzinfo=None).isoformat()},
            expire=WorkflowDurableRuntimeObserver.CANCEL_SIGNAL_TTL,
        )
        local_cancelled = WorkflowTaskRegistry.cancel(run_id)
        return {
            "run_id": run_id,
            "accepted": True,
            "local_cancelled": local_cancelled,
        }

    def _build_node_debug_graph(self, graph_def: WorkflowGraphDef, node_id: str) -> Dict[str, Any]:
        analyzer = WorkflowGraph(graph_def)
        node_map = {node.id: node for node in analyzer.all_nodes}
        if node_id not in node_map:
            raise NotFoundError("Workflow node not found.")

        ancestor_map = self._build_ancestor_map(analyzer)
        included = set(ancestor_map.get(node_id, set()))
        included.add(node_id)
        included.add(analyzer.start_node_id)

        target_node = node_map[node_id]
        selected_nodes = [node for node in graph_def.nodes if node.id in included]
        selected_edges = [
            edge
            for edge in graph_def.edges
            if edge.sourceNodeID in included and edge.targetNodeID in included
        ]

        if target_node.data.registryId == "End":
            return {
                "nodes": [node.model_dump(mode="json", by_alias=True, exclude_none=True) for node in selected_nodes],
                "edges": [edge.model_dump(mode="json", by_alias=True, exclude_none=True) for edge in selected_edges],
                "viewport": graph_def.viewport,
            }

        debug_end_inputs = []
        for schema in target_node.data.outputs or []:
            schema_copy = schema.model_copy(deep=True)
            schema_copy.value = RefValue(
                content=ValueRefContent(blockID=node_id, path=schema.name)
            )
            debug_end_inputs.append(schema_copy)

        debug_end = {
            "id": "__debug_end__",
            "data": {
                "registryId": "End",
                "name": f"DebugEnd:{target_node.data.name}",
                "inputs": [schema.model_dump(mode="json", by_alias=True, exclude_none=True) for schema in debug_end_inputs],
                "outputs": [],
                "config": {"returnType": "Object"},
            },
            "position": {"x": 0, "y": 0},
        }

        return {
            "nodes": [node.model_dump(mode="json", by_alias=True, exclude_none=True) for node in selected_nodes] + [debug_end],
            "edges": [edge.model_dump(mode="json", by_alias=True, exclude_none=True) for edge in selected_edges]
            + [
                {
                    "sourceNodeID": node_id,
                    "targetNodeID": "__debug_end__",
                    "sourcePortID": "0",
                    "targetPortID": "0",
                }
            ],
            "viewport": graph_def.viewport,
        }

    # --- Discovery & Tools ---

    async def get_searchable_content(self, instance: Workflow) -> str:
        texts = [instance.name, instance.description or ""]
        if instance.graph and "nodes" in instance.graph:
            for node in instance.graph["nodes"]:
                data = node.get("data", {})
                texts.append(data.get("name", ""))
        return " ".join(filter(None, texts))

    async def as_llm_tool(self, instance: Workflow) -> Optional[LLMTool]:
        properties = {}
        required = []
        inputs_schema_objs = [ParameterSchema(**s) for s in instance.inputs_schema]
        for param in inputs_schema_objs:
            if param.name:
                properties[param.name] = build_json_schema_node(param)
                if param.required:
                    required.append(param.name)
        
        return LLMTool(
            type="function",
            function=LLMToolFunction(
                name=f"call_workflow_{instance.uuid.replace('-', '_')}",
                description=instance.description or f"Execute workflow {instance.name}",
                parameters={"type": "object", "properties": properties, "required": required}
            )
        )

    # --- Internal Helpers ---

    async def _sync_references_incrementally(self, instance: Workflow, analyzer: WorkflowGraph, actor: User):
        """
        [Self-Healing Sync] 增量同步引用关系。
        """
        # 1. Target State (from DSL)
        dsl_refs: Set[Tuple[str, str]] = set()
        for node in analyzer.all_nodes:
            config = node.data.config
            # 仅提取明确定义的资源引用字段。
            # 约定：仅解析名为 'resource_instance_uuid' 的字段。
            if hasattr(config, "resource_instance_uuid"):
                res_uuid = getattr(config, "resource_instance_uuid")
                if isinstance(res_uuid, str) and res_uuid:
                    dsl_refs.add((res_uuid, node.id))

        # 2. Current State (from DB)
        existing_refs_orm = await self.ref_dao.get_dependencies(instance.id)
        db_refs_map: Dict[Tuple[str, str], ResourceRef] = {}
        for ref in existing_refs_orm:
            if ref.target_instance:
                key = (ref.target_instance.uuid, ref.source_node_uuid)
                db_refs_map[key] = ref

        db_refs_keys = set(db_refs_map.keys())

        # 3. Diff
        to_add = dsl_refs - db_refs_keys
        to_remove = db_refs_keys - dsl_refs

        # 4. Apply
        for key in to_remove:
            await self.db.delete(db_refs_map[key])

        for target_uuid, node_id in to_add:
            try:
                await self.ref_service.add_dependency(
                    source_instance_uuid=instance.uuid,
                    ref_data=ReferenceCreate(
                        target_instance_uuid=target_uuid,
                        source_node_uuid=node_id,
                        alias=f"Node_{node_id}_Ref",
                        context={"auto_synced": True}
                    ),
                    actor=actor
                )
            except Exception as e:
                logger.warning(f"Failed to sync reference for node {node_id}: {e}")
