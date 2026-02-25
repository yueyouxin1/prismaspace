import asyncio
from typing import List, Dict, Any, Tuple
from uuid import uuid4

# 引入定义和基类
from ..definitions import WorkflowNode, NodeData, WorkflowEdge, WorkflowGraphDef, NodeExecutionResult, NodeResultData, ParameterSchema
from ..registry import register_node, BaseNode
from ...utils.parameter_schema_utils import schemas2obj
from ...utils.data_parser import merge_dicts_vanilla
from .template import LOOP_TEMPLATE

@register_node(template=LOOP_TEMPLATE)
class LoopNode(BaseNode):
    """
    循环节点。
    逻辑复刻：动态构建子工作流，为每次迭代注入变量，并聚合结果。
    """

    def _create_standard_sub_workflow(
        self, 
        loop_node_id: str,
        inputs: List[ParameterSchema], 
        outputs: List[ParameterSchema], # 这里的 outputs 是经过筛选后的 loop_sub_outputs
        iteration_index: int, 
        iteration_item: Any
    ) -> Tuple[str, str, WorkflowGraphDef]:
        """
        核心方法：为单次迭代创建一个标准化的子工作流图。
        """
        # 1. 获取子图结构
        # 注意：WorkflowNode.data.blocks 是 List[WorkflowNode]，即已经是 Pydantic 对象
        sub_nodes_list = [n.model_copy(deep=True) for n in (self.node.data.blocks or [])]
        sub_edges_list = [e.model_copy(deep=True) for e in (self.node.data.edges or [])]
        
        # 2. 合成 Start/End 节点 ID
        synthetic_start_id = f"loop_{loop_node_id}_start_{iteration_index}"
        synthetic_end_id = f"loop_{loop_node_id}_end_{iteration_index}"

        # 3. 创建合成 Start 节点
        # 它不需要 inputs/outputs 定义，因为它主要作为图的起点
        synthetic_start_node = WorkflowNode(
            id=synthetic_start_id,
            data=NodeData(
                registryId="Start",
                name=f"Loop-{iteration_index}-Start",
                inputs=[],
                outputs=[]
            )
        )

        # 4. 创建合成 End 节点
        # 它的 inputs 应该是 Loop 节点的“内部输出定义” (loop_sub_outputs)
        # 这样子图中的节点连接到这个 End 节点时，数据结构能对齐
        synthetic_end_node = WorkflowNode(
            id=synthetic_end_id,
            data=NodeData(
                registryId="End",
                name=f"Loop-{iteration_index}-End",
                inputs=outputs, 
                outputs=[]
            )
        )

        # 5. 边重定向逻辑 (核心)
        standardized_edges = []
        for edge in sub_edges_list:
            source_id = edge.sourceNodeID
            target_id = edge.targetNodeID

            # A. 入口重定向: Loop (inline-output) -> Node  ==>  SyntheticStart -> Node
            # 原型判断：sourcePortID == 'loop-function-inline-output'
            if source_id == loop_node_id and edge.sourcePortID == 'loop-function-inline-output':
                standardized_edges.append(WorkflowEdge(
                    sourceNodeID=synthetic_start_id,
                    targetNodeID=target_id,
                    sourcePortID="0", # Start 默认端口
                    targetPortID=edge.targetPortID
                ))
            
            # B. 出口重定向: Node -> Loop (inline-input)  ==>  Node -> SyntheticEnd
            # 原型判断：targetPortID == 'loop-function-inline-input'
            elif target_id == loop_node_id and edge.targetPortID == 'loop-function-inline-input':
                standardized_edges.append(WorkflowEdge(
                    sourceNodeID=source_id,
                    targetNodeID=synthetic_end_id,
                    sourcePortID=edge.sourcePortID,
                    targetPortID="0" # 连入 End 的通常都归一化为 0? 或者根据 ParameterSchema 匹配？
                    # 原型中 End 的 inputs 是动态生成的，边连接到对应 index 的 input。
                    # 但在这里，edge.targetPortID 是 'loop-function-inline-input'。
                    # 实际上，Loop 内部连线通常是连到具体的 Handle。
                    # 原型逻辑：targetPortID="0"。这意味着 SyntheticEnd 只有一个默认入口？
                    # 不，End 节点通常通过 inputs schema 接收数据。
                    # 在原型中，`synthetic_end_node['data']['inputs'] = outputs`。
                    # 这里的 outputs 是 loop_sub_outputs。
                    # 如果有多条线连出来，它们应该连到 End 的不同 input handle。
                    # 但原型代码里写死为了 `targetPortID="0"`。
                    # 这意味着原型假设 Loop 内部输出只汇聚到一个点，或者通过变量引用解决。
                    # 我们严格复刻原型的这行代码：`"targetPortID": "0"`
                ))
            
            # C. 内部边保持不变
            else:
                standardized_edges.append(edge)

        # 6. 组装图
        nodes = [synthetic_start_node] + sub_nodes_list + [synthetic_end_node]
        graph_def = WorkflowGraphDef(
            nodes=nodes,
            edges=standardized_edges
        )
        
        return synthetic_start_id, synthetic_end_id, graph_def

    async def execute(self) -> NodeExecutionResult:
        # 1. 解析基础配置
        loop_node_id = self.node.id
        loop_type = self.node.data.config.loopType or 'count'
        
        # 解析循环控制参数 (count / list)
        loop_params = await schemas2obj(
            [self.node.data.config.loopCount, self.node.data.config.loopList], 
            self.context.variables
        )
        
        # 确定迭代数据源
        loop_data = []
        if loop_type == 'count':
            count_key = self.node.data.config.loopCount.name if self.node.data.config.loopCount else "loopCount"
            count = int(loop_params.get(count_key) or 0)
            loop_data = range(count)
        else:
            list_key = self.node.data.config.loopList.name if self.node.data.config.loopList else "loopList"
            raw_list = loop_params.get(list_key)
            loop_data = raw_list if isinstance(raw_list, list) else []

        # 解析 Loop 节点自身的 inputs (作为子流程的上下文基础)
        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)

        # 2. 分离输出定义 (LoopOutputs vs LoopSubOutputs)
        # 复刻原型逻辑：检查 value.type == 'ref' && source == 'loop-block-output'
        loop_outputs_schema = []     # 最终 Loop 节点对外输出的结构
        loop_sub_outputs_schema = [] # 子流程 End 节点需要收集的结构
        
        for schema in self.node.data.outputs:
            is_inner_output = False
            if schema.value and schema.value.type == 'ref':
                content = schema.value.content
                if isinstance(content, dict) and content.get('source') == 'loop-block-output':
                    is_inner_output = True
            
            if is_inner_output:
                # “拆壳”操作
                item_schema = schema.items # 既然是 Loop 输出，必然是 Array，其 items 定义了单次迭代的结构
                if not item_schema: 
                    # 如果定义不规范，回退使用 schema 本身或跳过
                    continue 

                # 构造子流程 End 节点的输入定义
                # 注意：这里我们需要把 SchemaBlueprint 转回 ParameterSchema
                # 并且名字要用 schema.name (外层名字)
                # 这样 merge_dicts_vanilla 才能正确聚合
                
                # 由于 ParameterSchema 继承自 SchemaBlueprint，我们可以直接构造
                # 但需要补全 name 等字段
                sub_output_item = ParameterSchema(
                    **item_schema.model_dump(exclude={'uid'}), # 排除 uid 避免冲突
                    name=schema.name,
                    # 复用外层的 value 定义 (虽然在 End 节点里它其实没用，因为数据是连线进来的)
                    # 但保持结构一致性
                    value=schema.value 
                )
                loop_sub_outputs_schema.append(sub_output_item)
            else:
                loop_outputs_schema.append(schema)

        all_iterations_results = []

        # 3. 串行执行迭代
        for index, item in enumerate(loop_data):
            # A. 构建子图
            start_id, end_id, sub_graph_def = self._create_standard_sub_workflow(
                loop_node_id,
                self.node.data.inputs,
                loop_sub_outputs_schema,
                index,
                item
            )

            # B. 准备子流程上下文
            # 注入循环变量 (index, item) 以及 Loop 节点的 inputs
            loop_variables = {
                "index": index,
                "item": item,
                **node_input
            }
            
            # 这里的 context key 必须是 loop_node_id
            # 这样子流程中的节点引用 {{LoopNodeID.item}} 才能解析正确
            context_for_subflow = {
                **self.context.variables,
                loop_node_id: loop_variables
            }

            # C. 创建子执行器并运行
            # 使用 self.context.create_sub_workflow_executor (IoC)
            sub_executor = self.context.create_sub_workflow_executor(
                workflow_data=sub_graph_def,
                parent_variables=context_for_subflow,
                payload=self.context.payload
            )
            
            # 执行子工作流
            # 这里调用的是 sub_executor.execute()，它是 Orchestrator 的方法
            # 它会返回 End 节点的 result

            iteration_result_raw = await sub_executor.execute()

            iteration_data = iteration_result_raw.output
            
            all_iterations_results.append(iteration_data)

        # 4. 聚合结果
        # 使用 merge_dicts_vanilla 将 list of dicts 转换为 dict of lists
        # [{"a": 1}, {"a": 2}] -> {"a": [1, 2]}
        loop_sub_result = await merge_dicts_vanilla(all_iterations_results)
        
        # 解析那些非内部引用的固定输出 (如果有)
        loop_def_result = await schemas2obj(loop_outputs_schema, self.context.variables)
        
        # 合并最终输出
        final_output = {**loop_sub_result, **loop_def_result}
        
        return NodeExecutionResult(input=node_input, data=NodeResultData(output=final_output))