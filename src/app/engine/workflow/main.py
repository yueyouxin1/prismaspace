from typing import Dict, Any, Type, Optional, List

from .definitions import WorkflowGraphDef, NodeResultData
from .orchestrator import WorkflowCallbacks, WorkflowOrchestrator
from .registry import default_node_registry, NodeExecutor
from .interceptor import NodeExecutionInterceptor
from . import nodes # 导入这些模块以触发 @register_node 装饰器自动注册

class WorkflowEngineService:
    """
    工作流引擎服务门面。
    负责初始化环境、解析定义并启动调度器。
    """

    async def run(
        self, 
        workflow_def: Dict[str, Any], 
        payload: Dict[str, Any] = None,
        callbacks: WorkflowCallbacks = None,
        external_context: Any = None,
        interceptors: Optional[List[NodeExecutionInterceptor]] = None
    ) -> NodeResultData:
        """
        执行工作流。
        :param workflow_def: 工作流定义的原始字典 (JSON)
        :param payload: 全局入参
        :param callbacks: 事件回调处理器
        :return: 最终执行结果
        """
        # 1. 解析并校验图结构
        try:
            graph_def = WorkflowGraphDef.model_validate(workflow_def)
        except Exception as e:
            raise ValueError(f"Invalid workflow definition: {e}")

        # 2. 创建调度器
        # Orchestrator 是纯粹的逻辑核心，每次执行都是独立的
        orchestrator = WorkflowOrchestrator(
            workflow_def=graph_def,
            payload=payload or {},
            callbacks=callbacks,
            external_context=external_context,
            interceptors=interceptors or [],
        )

        # 3. 启动执行
        try:
            result = await orchestrator.execute()
            return result
        except Exception as e:
            # 这里的异常通常是引擎内部未捕获的严重错误
            # 节点级的错误通常会被 Orchestrator 捕获并记录在状态中
            if callbacks:
                await callbacks.on_event("system_error", str(e))
            raise e
