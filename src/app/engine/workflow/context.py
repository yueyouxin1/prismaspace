from typing import Dict, Any, Literal, Optional
from pydantic import BaseModel, Field
from .definitions import NodeStatus, NodeResultData

class NodeState(BaseModel):
    node_id: str
    status: NodeStatus = "PENDING"
    input: Dict[str, Any] = Field(default_factory=dict, description="最终运行时输入")
    result: NodeResultData = Field(default_factory=NodeResultData, description="最终运行时输出")
    activated_port: str = "0"
    executed_time: float = 0.0

class WorkflowContext:
    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload
        self._variables: Dict[str, Any] = {}
        self._node_states: Dict[str, NodeState] = {}
        self._version: int = 0 

    @property
    def payload(self) -> Dict[str, Any]:
        return self._payload

    @property
    def variables(self) -> Dict[str, Any]:
        return self._variables
    
    @property
    def version(self) -> int:
        return self._version

    def init_node_state(self, node_id: str) -> NodeState:
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