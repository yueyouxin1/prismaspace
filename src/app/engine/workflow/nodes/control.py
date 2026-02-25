import re
import asyncio
from typing import Dict, Any, AsyncGenerator, List
from ...utils.parameter_schema_utils import schemas2obj
from ...utils.data_parser import get_value_by_path, get_value_by_expr_template
from ...utils.stream import StreamBroadcaster
from ..registry import register_node, BaseNode, WorkflowRuntimeContext
from ..definitions import WorkflowNode, NodeResultData, ParameterSchema, NodeExecutionResult, StreamEvent
from .template import START_TEMPLATE, END_TEMPLATE, OUTPUT_TEMPLATE, BRANCH_TEMPLATE

# ============================================================================
# 1. Start Node
# ============================================================================
@register_node(template=START_TEMPLATE)
class StartNode(BaseNode):
    """
    工作流开始节点。
    逻辑复刻：直接将全局 payload 映射到输出。
    """
    async def execute(self) -> NodeExecutionResult:
        # 这里的 ready_params 就是工作流的全局入参 (self.context.payload)
        # schemas2obj 会根据 outputs 定义的结构，从 payload 中提取数据
        node_input = await schemas2obj(
            target_schema=self.node.data.outputs, 
            context=self.context.variables, 
            real_data=self.context.payload
        )
        
        return NodeExecutionResult(input=node_input, data=NodeResultData(output=node_input))

# ============================================================================
# 2. Output Node
# ============================================================================
@register_node(template=OUTPUT_TEMPLATE)
class OutputNode(BaseNode):
    """
    中间输出节点。用于在工作流执行过程中输出调试信息或中间结果。
    """
    async def execute(self) -> NodeExecutionResult:
        return NodeExecutionResult(data=NodeResultData(output={}))

# ============================================================================
# 3. End Node (核心流式逻辑)
# ============================================================================
@register_node(template=END_TEMPLATE)
class EndNode(BaseNode):
    """
    工作流结束节点。
    核心职责：生成最终响应。如果配置了流式输出，需要处理流的拼接和透传。
    """

    async def _stream_content_handler(self, template: str) -> AsyncGenerator[str, None]:
        parts = re.split(r'(\{\{[^}]+\}\})', template)
        
        # 缓存解析后的参数，避免重复计算
        cached_params = {}
        last_seen_version = -1

        for part in parts:
            if not part: continue

            match = re.match(r'\{\{([^}]+)\}\}', part)
            if not match:
                yield part
                continue

            variable_path = match.group(1).strip()
            ref_details = self.context.get_ref_details(self.node.id, variable_path)
            block_id = ref_details.get('blockID') if ref_details else None

            source_data = None
            if block_id:
                source_data = self.context.variables.get(block_id)

            # [Case 1] 源是流广播器 -> 走流式快速通道
            if isinstance(source_data, StreamBroadcaster):
                path_in_chunk = ref_details.get('path', '')
                stream_generator = source_data.subscribe()
                async for chunk in stream_generator:
                    value = chunk if isinstance(chunk, str) else await get_value_by_path(chunk, path_in_chunk)
                    if value is not None:
                        yield str(value)
            
            # [Case 2] 源是静态数据 -> 走标准解析通道 (复刻原型)
            else:
                # 检查上下文版本，如果变了就重新解析 inputs
                # 注意：这里依赖 Context 暴露 version 属性，我们在 Context 类里已经加了
                current_version = getattr(self.context, 'version', 0) # 兼容性写法
                
                if current_version > last_seen_version:
                    # 重新解析 inputs 生成本地变量字典
                    cached_params = await schemas2obj(
                        self.node.data.inputs, 
                        self.context.variables
                    )
                    last_seen_version = current_version
                
                # 从解析好的本地参数中直接取值
                value = await get_value_by_path(cached_params, variable_path)
                if value is not None:
                    yield str(value)

    async def execute(self) -> NodeExecutionResult:
        stream = self.node.data.config.stream # 对应原型的 config.stream
        return_type = self.node.data.config.returnType # 对应原型的 config.returnType
        content_template = self.node.data.config.content

        # 场景 A: 开启流式 且 返回类型为 Text 且 有模板
        if stream and return_type == 'Text' and content_template:
            final_content = ""
            try:
                # 通知前端流开始
                await self.context.send('stream_start', StreamEvent(
                    node_id=self.node.id,
                    status='STREAMSTART'
                ))

                # 执行流式拼接
                async for chunk in self._stream_content_handler(content_template):
                    final_content += chunk
                    # 实时推送 chunk
                    await self.context.send('stream_chunk', StreamEvent(
                        node_id=self.node.id,
                        content=chunk,
                        status='STREAMING'
                    ))

                # 通知流结束
                await self.context.send('stream_end', StreamEvent(
                    node_id=self.node.id,
                    status='STREAMEND'
                ))

                # 流式处理完成后，生成最终的结构化 output
                final_params = await schemas2obj(self.node.data.inputs, self.context.variables)
                
                return NodeExecutionResult(input=final_params, data=NodeResultData(output=final_params, content=final_content))

            except Exception as e:
                print(f"Error in End node streaming: {e}")
                return NodeExecutionResult(data=NodeResultData(error_msg=str(e)))

        # 场景 B: 普通非流式返回
        else:
            final_params = await schemas2obj(self.node.data.inputs, self.context.variables)
            content = None
            if return_type == 'Text' and content_template:
                content = await get_value_by_expr_template(content_template, final_params)

            return NodeExecutionResult(input=final_params, data=NodeResultData(output=final_params, content=content))

# ============================================================================
# 4. Branch Node
# ============================================================================
@register_node(template=BRANCH_TEMPLATE)
class BranchNode(BaseNode):
    """
    分支节点。
    逻辑复刻：根据配置的条件列表，计算出激活的端口 ID (activated_port)。
    """
    
    # 复刻原型中的操作符映射
    OPERATOR_MAPPING = {
        1: lambda x, y: x == y,
        2: lambda x, y: x != y,
        3: lambda x, y: len(x) > y if hasattr(x, '__len__') else False,
        4: lambda x, y: len(x) >= y if hasattr(x, '__len__') else False,
        5: lambda x, y: len(x) < y if hasattr(x, '__len__') else False,
        6: lambda x, y: len(x) <= y if hasattr(x, '__len__') else False,
        7: lambda x, y: str(y) in str(x) if x is not None else False,
        8: lambda x, y: str(y) not in str(x) if x is not None else False,
        9: lambda x, y: x is None or x == '',
        10: lambda x, y: x is not None and x != ''
    }

    async def _get_val(self, param_schema: ParameterSchema) -> Any:
        """辅助函数：解析 ParameterSchema 的值 (可能是引用，也可能是字面量)"""
        # 利用 schemas2obj 解析单个字段
        # 我们构造一个临时的 list[ParameterSchema] 传给 schemas2obj
        # schemas2obj 返回 dict，我们取其中的值
        temp_key = "temp_val"
        # 为了不破坏原始对象，我们需要浅拷贝一下 schema 并赋予名字
        # 但 ParameterSchema 是 Pydantic，直接用 schemas2obj 处理单个项的内部逻辑比较复杂
        # 这里为了简单高效，直接复用 schemas2obj 的能力
        
        # 构造一个临时的 schema 列表
        schema_copy = param_schema.model_copy()
        schema_copy.name = temp_key
        
        result_dict = await schemas2obj([schema_copy], self.context.variables)
        return result_dict.get(temp_key)

    async def execute(self) -> NodeExecutionResult:
        activated_port = '-1' # 默认无匹配
        branchs = self.node.data.config.branchs or []

        for branch_index, branch in enumerate(branchs):
            logic = branch.logic # AND / OR
            conditions = branch.conditions

            if not conditions:
                continue
            
            # 计算该分支下所有条件的结果
            results = []
            for condition in conditions:
                # 原型逻辑：left 和 right 都是 value 对象
                # 这里的 left/right 是 ParameterSchema 类型
                left_val = await self._get_val(condition.left)
                right_val = await self._get_val(condition.right)
                operator_id = condition.operator

                op_func = self.OPERATOR_MAPPING.get(operator_id, lambda x, y: False)
                try:
                    res = op_func(left_val, right_val)
                except Exception:
                    res = False
                results.append(res)

            # 根据逻辑关系判断分支是否成立
            is_branch_active = False
            if logic == '&': # AND
                is_branch_active = all(results)
            elif logic == '|': # OR
                is_branch_active = any(results)
            
            if is_branch_active:
                activated_port = str(branch_index)
                break # 找到第一个满足的分支即停止 (if-else if 逻辑)

        # Branch 节点本身不输出数据，只决定流向
        return NodeExecutionResult(input={}, data=NodeResultData(output={}), activated_port=activated_port)