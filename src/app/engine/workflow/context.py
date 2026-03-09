from typing import Dict, Any
from pydantic import BaseModel, Field
from .definitions import NodeStatus, NodeResultData

class NodeState(BaseModel):
    node_id: str
    status: NodeStatus = "PENDING"
    input: Dict[str, Any] = Field(default_factory=dict, description="最终运行时输入")
    result: NodeResultData = Field(default_factory=NodeResultData, description="最终运行时输出")
    activated_port: str = "0"
    executed_time: float = 0.0


class WorkflowRuntimeSnapshot(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict)
    node_states: Dict[str, NodeState] = Field(default_factory=dict)
    ready_queue: list[str] = Field(default_factory=list)
    version: int = 0
    step_index: int = 0

class WorkflowContext:
    def __init__(
        self,
        payload: Dict[str, Any],
        variables: Dict[str, Any] | None = None,
        node_states: Dict[str, NodeState] | None = None,
        version: int = 0,
    ):
        self._payload = payload
        self._variables: Dict[str, Any] = dict(variables or {})
        self._node_states: Dict[str, NodeState] = dict(node_states or {})
        self._version: int = version

    @classmethod
    def from_snapshot(cls, snapshot: WorkflowRuntimeSnapshot) -> "WorkflowContext":
        return cls(
            payload=dict(snapshot.payload or {}),
            variables=dict(snapshot.variables or {}),
            node_states={
                node_id: state if isinstance(state, NodeState) else NodeState.model_validate(state)
                for node_id, state in (snapshot.node_states or {}).items()
            },
            version=snapshot.version,
        )

    @property
    def payload(self) -> Dict[str, Any]:
        return self._payload

    @property
    def variables(self) -> Dict[str, Any]:
        return self._variables
    
    @property
    def version(self) -> int:
        return self._version

    @property
    def node_states(self) -> Dict[str, NodeState]:
        return self._node_states

    def init_node_state(self, node_id: str) -> NodeState:
        if node_id not in self._node_states:
            self._node_states[node_id] = NodeState(node_id=node_id)
        return self._node_states[node_id]

    def get_node_state(self, node_id: str) -> NodeState:
        return self._node_states[node_id]

    def update_node_state(self, node_id: str, **kwargs) -> NodeState:
        state = self._node_states[node_id]
        updated = state.model_copy(update=kwargs)
        self._node_states[node_id] = updated
        return self._node_states[node_id]

    def set_variable(self, node_id: str, value: Any) -> Any:
        self._variables[node_id] = value
        self._version += 1
        return self._variables[node_id]

    def snapshot(self, ready_queue: list[str], step_index: int) -> WorkflowRuntimeSnapshot:
        return WorkflowRuntimeSnapshot(
            payload=dict(self._payload),
            variables=dict(self._variables),
            node_states=dict(self._node_states),
            ready_queue=list(ready_queue),
            version=self._version,
            step_index=step_index,
        )
