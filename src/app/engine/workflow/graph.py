import networkx as nx
from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict
from .definitions import WorkflowGraphDef, WorkflowNode

class WorkflowGraph:
    """
    工作流图结构的静态分析容器。
    完全复刻 WorkflowValidator 的结构分析能力。
    """
    def __init__(self, graph_def: WorkflowGraphDef):
        self._def = graph_def
        self._nodes_map: Dict[str, WorkflowNode] = {n.id: n for n in graph_def.nodes}
        self._start_node: Optional[WorkflowNode] = None
        self._end_node: Optional[WorkflowNode] = None
        self._nx_graph = nx.DiGraph()
        
        # 索引: (source_node_id, source_port_id) -> List[target_node_id]
        self._edge_index: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        
        self._build_and_validate()

    @property
    def start_node(self) -> WorkflowNode:
        if self._start_node is None:
            raise ValueError("Start node not initialized.")
        return self._start_node

    @property
    def start_node_id(self) -> str:
        return self.start_node.id

    @property
    def end_node(self) -> WorkflowNode:
        if self._end_node is None:
            raise ValueError("End node not initialized.")
        return self._end_node

    @property
    def end_node_id(self) -> str:
        return self.end_node.id

    @property
    def all_nodes(self) -> List[WorkflowNode]:
        return self._def.nodes

    def get_node(self, node_id: str) -> WorkflowNode:
        if node_id not in self._nodes_map:
            raise KeyError(f"Node {node_id} not found in graph.")
        return self._nodes_map[node_id]

    def get_successors(self, node_id: str) -> List[str]:
        if node_id not in self._nx_graph: return []
        return list(self._nx_graph.successors(node_id))

    def get_predecessors(self, node_id: str) -> List[str]:
        if node_id not in self._nx_graph: return []
        return list(self._nx_graph.predecessors(node_id))

    def get_targets_from_port(self, node_id: str, port_id: str) -> List[str]:
        return self._edge_index.get((node_id, port_id), [])

    def _build_and_validate(self):
        # 1. 构建图
        start_nodes = []
        end_nodes = []

        for node in self._def.nodes:
            self._nx_graph.add_node(node.id)
            if node.data.registryId == 'Start':
                start_nodes.append(node)
            elif node.data.registryId == 'End':
                end_nodes.append(node)

        # 校验：必须有且仅有一个 Start 节点
        if len(start_nodes) != 1:
            raise ValueError(f"Workflow must have exactly one 'Start' node. Found {len(start_nodes)}.")
        self._start_node = start_nodes[0]

        # 校验：必须有且仅有一个 End 节点
        if len(end_nodes) != 1:
            raise ValueError(f"Workflow must have exactly one 'End' node. Found {len(end_nodes)}.")
        self._end_node = end_nodes[0]

        # --- 2. 构建边 ---
        for edge in self._def.edges:
            self._nx_graph.add_edge(edge.sourceNodeID, edge.targetNodeID)
            self._edge_index[(edge.sourceNodeID, edge.sourcePortID)].append(edge.targetNodeID)

        # --- 3. 结构连通性检查 ---
        if not nx.is_directed_acyclic_graph(self._nx_graph):
            raise ValueError("Workflow contains cycles (DAG violation).")
            
        # 检查孤立节点
        if len(self._nodes_map) > 0:
            start_id = self.start_node_id
            descendants = nx.descendants(self._nx_graph, start_id)
            descendants.add(start_id)
            all_ids = set(self._nodes_map.keys())
            unreachable = all_ids - descendants
            if unreachable:
                raise ValueError(f"Unreachable nodes found: {unreachable}")