from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .definitions import BaseNodeConfig, NodeData, ParameterSchema, WorkflowEdge, WorkflowGraphDef, WorkflowNode
from .graph import WorkflowGraph


class WorkflowRuntimeNodeSpec(BaseModel):
    id: str
    registry_id: str
    name: str
    description: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)
    inputs: List[ParameterSchema] = Field(default_factory=list)
    outputs: List[ParameterSchema] = Field(default_factory=list)
    blocks: Optional[List["WorkflowRuntimeNodeSpec"]] = None
    edges: Optional[List[WorkflowEdge]] = None

    model_config = ConfigDict(extra="forbid")

    def to_workflow_node(self) -> WorkflowNode:
        return WorkflowNode(
            id=self.id,
            data=NodeData(
                registryId=self.registry_id,
                name=self.name,
                description=self.description,
                config=BaseNodeConfig.model_validate(self.config),
                inputs=list(self.inputs or []),
                outputs=list(self.outputs or []),
                blocks=[block.to_workflow_node() for block in self.blocks] if self.blocks else None,
                edges=list(self.edges or []) if self.edges else None,
            ),
        )


class WorkflowRuntimePlan(BaseModel):
    version: str = "runtime-v1"
    start_node_id: str
    end_node_id: str
    nodes: List[WorkflowRuntimeNodeSpec]
    edges: List[WorkflowEdge] = Field(default_factory=list)
    predecessors_by_node: Dict[str, List[str]] = Field(default_factory=dict)
    successors_by_node: Dict[str, List[str]] = Field(default_factory=dict)
    port_routes: Dict[str, Dict[str, List[str]]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    def get_node(self, node_id: str) -> WorkflowRuntimeNodeSpec:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(f"Node {node_id} not found in runtime plan.")

    @property
    def all_nodes(self) -> List[WorkflowRuntimeNodeSpec]:
        return self.nodes

    @property
    def start_node(self) -> WorkflowRuntimeNodeSpec:
        return self.get_node(self.start_node_id)

    @property
    def end_node(self) -> WorkflowRuntimeNodeSpec:
        return self.get_node(self.end_node_id)

    def get_successors(self, node_id: str) -> List[str]:
        return list(self.successors_by_node.get(node_id, []))

    def get_predecessors(self, node_id: str) -> List[str]:
        return list(self.predecessors_by_node.get(node_id, []))

    def get_targets_from_port(self, node_id: str, port_id: str) -> List[str]:
        return list(self.port_routes.get(node_id, {}).get(port_id, []))


class WorkflowRuntimeCompiler:
    """
    将编辑态/持久化 DSL 编译为运行时 IR。
    运行时 IR 去除画布坐标等 UI 细节，并预计算调度所需的邻接关系。
    """

    def compile(self, workflow_def: WorkflowGraphDef | Dict[str, Any]) -> WorkflowRuntimePlan:
        graph_def = (
            workflow_def
            if isinstance(workflow_def, WorkflowGraphDef)
            else WorkflowGraphDef.model_validate(workflow_def)
        )
        analyzer = WorkflowGraph(graph_def)

        port_routes: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for edge in graph_def.edges:
            port_routes[edge.sourceNodeID][edge.sourcePortID].append(edge.targetNodeID)

        return WorkflowRuntimePlan(
            start_node_id=analyzer.start_node_id,
            end_node_id=analyzer.end_node_id,
            nodes=[self._compile_node(node) for node in analyzer.all_nodes],
            edges=list(graph_def.edges),
            predecessors_by_node={
                node.id: analyzer.get_predecessors(node.id)
                for node in analyzer.all_nodes
            },
            successors_by_node={
                node.id: analyzer.get_successors(node.id)
                for node in analyzer.all_nodes
            },
            port_routes={
                node_id: dict(routes)
                for node_id, routes in port_routes.items()
            },
        )

    def _compile_node(self, node: WorkflowNode) -> WorkflowRuntimeNodeSpec:
        return WorkflowRuntimeNodeSpec(
            id=node.id,
            registry_id=node.data.registryId,
            name=node.data.name,
            description=node.data.description,
            config=node.data.config.model_dump(mode="json", exclude_none=True),
            inputs=list(node.data.inputs or []),
            outputs=list(node.data.outputs or []),
            blocks=[self._compile_node(block) for block in (node.data.blocks or [])] or None,
            edges=list(node.data.edges or []) or None,
        )


WorkflowRuntimeNodeSpec.model_rebuild()
