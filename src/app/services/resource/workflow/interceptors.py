from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trace_manager import TraceManager
from app.services.auditing.types.attributes import WorkflowNodeMeta, WorkflowNodeAttributes
from app.engine.workflow.interceptor import NodeExecutionInterceptor, NextCall
from app.engine.workflow.definitions import WorkflowNode
from app.engine.workflow.context import WorkflowContext, NodeState


class WorkflowTraceInterceptor(NodeExecutionInterceptor):
    def __init__(self, db: AsyncSession, user_id: int, workflow_trace_id: str):
        self.db = db
        self.user_id = user_id
        self.workflow_trace_id = workflow_trace_id

    async def intercept(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        next_call: NextCall
    ) -> NodeState:
        registry_id = node.data.registryId
        node_name = node.data.name
        node_config = node.data.config.model_dump(mode='json', exclude_none=True)

        trace_attrs = WorkflowNodeAttributes(
            meta=WorkflowNodeMeta(
                node_id=node.id,
                node_name=node_name,
                node_method=registry_id,
                node_config=node_config
            )
        )

        async with TraceManager(
            db=self.db,
            operation_name=f"workflow.node.{registry_id.lower()}.{node.id}",
            user_id=self.user_id,
            force_trace_id=self.workflow_trace_id,
            attributes=trace_attrs
        ) as span:
            try:
                node_state = await next_call()
                if node_state.input:
                    span.set_input(node_state.input)
                span.set_output(node_state.result)
                return node_state
            except Exception as e:
                span.set_error(e)
                raise e
