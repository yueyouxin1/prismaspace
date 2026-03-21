# src/app/services/resource/workflow/workflow_service.py

import logging
from typing import Dict, Any, List, Optional, Set, Tuple

from pydantic import ValidationError
from sqlalchemy import func

from app.core.context import AppContext
from app.db.session import SessionLocal
from app.utils.async_generator import AsyncGeneratorManager
from app.models import (
    ResourceExecution,
    User,
    Workspace,
    Resource,
    Workflow,
    VersionStatus,
)
from app.dao.resource.workflow.workflow_dao import WorkflowDao, WorkflowNodeDefDao
from app.dao.resource.resource_ref_dao import ResourceRefDao
from app.schemas.protocol import WORKFLOW_DEFAULT_PROTOCOL
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowEventRead,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    WorkflowInterruptRead,
    WorkflowRead,
    WorkflowRunRead,
    WorkflowRunSummaryRead,
    WorkflowUpdate,
)
from app.schemas.resource.resource_ref_schemas import ReferenceCreate
from app.services.resource.base.base_impl_service import register_service, ResourceImplementationService, ValidationResult, DependencyInfo
from app.services.resource.execution.execution_ledger_service import ExecutionLedgerService
from app.services.resource.resource_ref_service import ResourceRefService
from app.services.exceptions import ServiceException, NotFoundError
from app.services.resource.workflow.protocol_adapter.registry import build_default_workflow_protocol_registry
from app.services.resource.workflow.event_log_service import WorkflowEventLogService
from app.services.resource.workflow.live_events import WorkflowLiveEventBuffer, WorkflowLiveEventService
from app.services.resource.workflow.run_control import WorkflowRunControlService
from app.services.resource.workflow.run_execution import WorkflowRunExecutionService, WorkflowStreamCallbacks
from app.services.resource.workflow.run_preparation import WorkflowRunPreparationService
from app.services.resource.workflow.run_query import WorkflowRunQueryService
from app.services.resource.workflow.runtime_persistence import WorkflowRuntimePersistenceService
from app.services.resource.workflow.runtime_runner import WorkflowRuntimeRunner
from app.services.resource.workflow.types.workflow import ExternalContext, PreparedWorkflowRun, WorkflowRunResult
# Engine Imports
from app.engine.workflow import (
    ParameterSchema,
    WorkflowEngineService,
    WorkflowGraph,
    WorkflowGraphDef,
    WorkflowRuntimeCompiler,
    WorkflowRuntimePlan,
    WorkflowRuntimeSnapshot,
)
from app.engine.utils.parameter_schema_utils import build_json_schema_node
from app.engine.schemas.parameter_schema import RefValue, ValueRefContent
from app.engine.model.llm import LLMTool, LLMToolFunction

logger = logging.getLogger(__name__)

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
        self.protocol_adapters = build_default_workflow_protocol_registry()
        self.runtime_compiler = WorkflowRuntimeCompiler()
        self.run_query_service = WorkflowRunQueryService(self)
        self.run_control_service = WorkflowRunControlService(self)
        self.run_execution_service = WorkflowRunExecutionService(self)
        self.run_preparation_service = WorkflowRunPreparationService(self)
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

    def resolve_protocol_adapter(self, protocol: Optional[str] = None):
        resolved = (protocol or WORKFLOW_DEFAULT_PROTOCOL).strip().lower()
        adapter = self.protocol_adapters.get(resolved)
        if adapter is None:
            raise ServiceException(f"Workflow protocol '{resolved}' is reserved but not implemented yet.")
        return adapter

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
        return await self.run_execution_service.execute(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

    async def execute_batch(
        self,
        instance_uuids: List[str],
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> List[WorkflowExecutionResponse]:
        return await self.run_execution_service.execute_batch(
            instance_uuids=instance_uuids,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

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
        return await self.run_execution_service.enqueue_background_execute(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

    async def _prepare_async_run(
        self,
        *,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> PreparedWorkflowRun:
        return await self.run_execution_service.prepare_async_run(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

    async def _run_workflow_background_task(
        self,
        *,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        runtime_plan: WorkflowRuntimePlan,
        restored_snapshot: Optional[WorkflowRuntimeSnapshot],
        payload: Dict[str, Any],
        callbacks: WorkflowStreamCallbacks,
        generator_manager: AsyncGeneratorManager,
        external_context: ExternalContext,
        trace_id: str,
        actor: User,
        live_event_buffer: Optional[WorkflowLiveEventBuffer] = None,
    ) -> None:
        await self.run_execution_service.run_background_task(
            execution=execution,
            workflow_instance=workflow_instance,
            runtime_plan=runtime_plan,
            restored_snapshot=restored_snapshot,
            payload=payload,
            callbacks=callbacks,
            generator_manager=generator_manager,
            external_context=external_context,
            trace_id=trace_id,
            actor=actor,
            live_event_buffer=live_event_buffer,
        )

    async def execute_precreated_run(
        self,
        *,
        run_id: str,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> None:
        await self.run_execution_service.execute_precreated_run(
            run_id=run_id,
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
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
        return await self.run_preparation_service.prepare_run_context(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
            existing_run_id=existing_run_id,
        )

    def _build_event_persister(self, *, execution, workflow_instance: Workflow):
        return self.run_execution_service.build_event_persister(
            execution=execution,
            workflow_instance=workflow_instance,
        )

    def _to_run_summary(self, execution, latest_checkpoint) -> WorkflowRunSummaryRead:
        return self.run_query_service.build_run_summary(execution, latest_checkpoint)

    async def _get_latest_interrupt(
        self,
        *,
        execution_id: int,
    ) -> Optional[WorkflowInterruptRead]:
        return await self.run_query_service.get_latest_interrupt(execution_id=execution_id)

    async def _resolve_resume_payload(
        self,
        *,
        parent_execution,
        execute_params: WorkflowExecutionRequest,
    ) -> Any:
        return await self.run_query_service.resolve_resume_payload(
            parent_execution=parent_execution,
            execute_params=execute_params,
        )

    async def get_run(self, run_id: str) -> WorkflowRunRead:
        return await self.run_query_service.get_run(run_id)

    async def list_run_events(
        self,
        run_id: str,
        *,
        limit: int = 1000,
    ) -> List[WorkflowEventRead]:
        return await self.run_query_service.list_run_events(
            run_id,
            limit=limit,
        )

    async def stream_live_run_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
    ):
        async for envelope in self.run_query_service.stream_live_run_events(
            run_id,
            after_seq=after_seq,
        ):
            yield envelope

    async def list_runs(
        self,
        instance_uuid: str,
        *,
        limit: int = 20,
    ) -> List[WorkflowRunSummaryRead]:
        return await self.run_query_service.list_runs(instance_uuid, limit=limit)

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
        return await self.run_preparation_service.build_debug_node_request(
            instance_uuid=instance_uuid,
            node_id=node_id,
            execute_params=execute_params,
        )

    async def cancel_run(self, run_id: str) -> Dict[str, Any]:
        return await self.run_control_service.cancel_run(run_id)

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
