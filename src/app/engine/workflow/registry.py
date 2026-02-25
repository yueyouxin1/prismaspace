from typing import Dict, Type, Any, Optional, Protocol, Union
from .definitions import WorkflowNode, NodeExecutionResult, NodeResultData, NodeData, NodeTemplate

# ============================================================================
# 1. 协议定义 (Protocols)
# ============================================================================

class WorkflowRuntimeContext(Protocol):
    """
    [依赖倒置] 定义节点在执行过程中能访问的引擎能力。
    这对应原型中节点类里的 `self.executor`。
    任何传递给节点的 'executor' 或 'context' 必须实现此协议。
    """
    @property
    def variables(self) -> Dict[str, Any]:
        """获取当前工作流的变量池 (Read/Write)"""
        ...
    
    @property
    def payload(self) -> Dict[str, Any]:
        """获取工作流的全局入参"""
        ...

    @property
    def version(self) -> int:
        ...

    @property
    def external_context(self) -> Any:
        ...

    async def send(self, type: str, data: Any = None) -> None:
        """发送事件通知 (对应原型的 executor.send)"""
        ...
        
    def create_sub_workflow_executor(self, workflow_data: Dict[str, Any], context: Dict[str, Any]) -> Any:
        """
        [关键] 允许 Loop 节点创建子工作流执行器。
        返回的对象应具有 execute() 方法。
        """
        ...

    def get_ref_details(self, consumer_node_id: str, variable_path: str) -> Optional[Dict]:
        """允许节点查询变量引用的源头 (用于 End 节点的流式拼接)"""
        ...

class NodeExecutor(Protocol):
    """
    [核心契约] 所有节点实现类（无论是基础的还是扩展的）必须遵循的接口。
    """
    def __init__(
        self, 
        context: WorkflowRuntimeContext, 
        node: WorkflowNode, 
        is_stream_producer: bool
    ):
        """
        初始化节点执行器。
        :param context: 运行时上下文，提供变量访问、事件发送等能力
        :param node: 当前节点的完整定义 (Pydantic Model)
        :param is_stream_producer: 标记当前节点是否被下游流式消费 (图编译阶段计算得出)
        """
        ...

    async def execute(self) -> NodeExecutionResult:
        """
        执行节点逻辑。
        :return: 标准化的执行结果
        """
        ...

class BaseNode:
    """
    所有节点的通用基类。
    负责处理标准的初始化逻辑，将参数绑定到实例属性。
    """
    # 静态属性，存储该节点类型的模版定义
    template: NodeTemplate = None 

    def __init__(
        self, 
        context: WorkflowRuntimeContext, 
        node: WorkflowNode, 
        is_stream_producer: bool
    ):
        self.context = context
        self.node = node
        self.node.data.config = self.template.data.config.model_validate(node.data.config.model_dump())
        self.is_stream_producer = is_stream_producer
        
# ============================================================================
# 3. 注册中心 (Registry)
# ============================================================================

class NodeRegistry:
    """
    节点注册中心。
    """
    def __init__(self):
        self._executors: Dict[str, Type[NodeExecutor]] = {}
        self._templates: Dict[str, NodeTemplate] = {}

    def register(self, template: NodeTemplate):
        """
        [Upgrade] 注册节点，必须提供完整的 NodeTemplate。
        """
        if not isinstance(template, NodeTemplate):
            raise TypeError("Must register with a NodeTemplate instance.")
        
        registryId = template.data.registryId

        def decorator(cls):
            # 将 template 绑定到类上
            cls.template = template
            
            self._executors[registryId] = cls
            self._templates[registryId] = template
            return cls
        return decorator

    def get(self, registry_id: str) -> Type[NodeExecutor]:
        executor_cls = self._executors.get(registry_id)
        if not executor_cls:
            raise ValueError(f"No executor registered for node registry id '{registry_id}'.")
        return executor_cls

    def has(self, registry_id: str) -> bool:
        return registry_id in self._executors

    def get_all_templates(self) -> Dict[str, NodeTemplate]:
        """获取所有注册节点的模版"""
        return self._templates

# ============================================================================
# 4. 全局实例与辅助函数 (Global Instance & Helpers)
# ============================================================================

# 创建一个默认的全局注册表，方便上层直接使用装饰器
default_node_registry = NodeRegistry()
register_node = default_node_registry.register