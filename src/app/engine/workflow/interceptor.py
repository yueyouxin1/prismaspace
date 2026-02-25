from typing import Protocol, Callable, Awaitable, Any, List
from .definitions import WorkflowNode
from .context import WorkflowContext, NodeState

# 定义 Next 函数的签名：一个不接受参数、返回 NodeState 的异步函数
NextCall = Callable[[], Awaitable[NodeState]]

class NodeExecutionInterceptor(Protocol):
    """
    [纯粹引擎协议] 节点执行拦截器。
    
    设计原则：
    1. 纯粹性：不依赖 App 层模型（如 User, Trace）。
    2. 包裹性：控制执行的前（Pre）、中（Exec）、后（Post）以及异常（Exception）。
    """
    async def intercept(
        self, 
        node: WorkflowNode, 
        context: WorkflowContext, 
        next_call: NextCall
    ) -> NodeState:
        """
        拦截逻辑。必须在逻辑中调用 await next_call() 来继续执行链。
        """
        ...