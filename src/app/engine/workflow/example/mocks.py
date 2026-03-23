import asyncio
import ast
import json
from typing import Dict, Any, List
from pydantic import Field, ConfigDict
from ..definitions import WorkflowGraphDef, WorkflowNode, NodeData, BaseNodeConfig, NodeExecutionResult, NodeResultData, NodeTemplate, NodeCategory, StreamEvent
from ..context import NodeState
from ..registry import register_node, BaseNode
from ...utils.parameter_schema_utils import schemas2obj
from ...utils.stream import StreamBroadcaster
from ...schemas.parameter_schema import ParameterSchema 

# --- 权威定义该节点的配置结构 ---
class LLMNodeConfig(BaseNodeConfig):
    model: str = Field(..., description="模型名称，如 gpt-4")
    system_prompt: str = Field(default="")
    input_query: str = Field("", description="用户输入")
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0)
    response_format: str = Field(default="text")
    model_config = ConfigDict(extra="forbid")

MockLLM_TEMPLATE = NodeTemplate(
    category=NodeCategory.MODEL,
    icon="cpu",
    # 核心预设数据 (NodeData)
    data=NodeData(
        registryId="MockLLM",
        name="大语言模型",
        description="调用系统集成的 LLM 模型进行文本生成。",
        # 预设参数
        inputs=[],
        outputs=[ParameterSchema(name="text", type="string", label="生成结果")],
        # 预设配置 (使用 Config 类的默认值)
        config=LLMNodeConfig(model="gpt-4o")
    )
)

@register_node(template=MockLLM_TEMPLATE)
class MockLLMNode(BaseNode):
    """
    生产级 LLM 节点参考实现。
    支持 Text, Markdown, JSON 三种模式的流式生成与结构化输出。
    """
    async def execute(self) -> NodeExecutionResult:
        # 1. 解析配置与输入
        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        prompt = self.node.data.config.system_prompt
        input_query = node_input.get("input_query")
        if input_query is None:
            input_query = self.node.data.config.input_query
        input_query = str(input_query or "")
        
        # 获取输出模式，默认为 text
        # 这里的 config 结构对应前端的配置项
        # 假设 config 结构: { "response_format": "json" | "text" | "markdown" }
        response_format = getattr(self.node.data.config, "response_format", "text") 

        outputs_schema = self.node.data.outputs or []
        
        # 2. 准备流广播器
        broadcaster = None
        if self.is_stream_producer:
            broadcaster = StreamBroadcaster(self.node.id)

        # 3. 定义生成逻辑 (根据模式策略)

        def _mock_string_value(field_name: str, field_path: str) -> str:
            normalized_query = input_query.removeprefix("SAFE_")
            lower_name = field_name.lower()

            if lower_name in {"product_name", "product", "name", "item"}:
                return normalized_query

            if lower_name in {"score", "rating"}:
                checksum = sum(ord(ch) for ch in normalized_query)
                return str(60 + (checksum % 41)) if normalized_query else "0"

            return f"Parsed value for {field_path} based on '{input_query}'"

        def _build_json_value(schema: ParameterSchema, field_path: str) -> Any:
            schema_type = schema.type
            field_name = schema.name or field_path

            if schema_type == "string":
                return _mock_string_value(field_name, field_path)
            if schema_type == "integer":
                checksum = sum(ord(ch) for ch in input_query)
                return checksum % 100
            if schema_type == "number":
                checksum = sum(ord(ch) for ch in input_query)
                return float(checksum % 100)
            if schema_type == "boolean":
                return bool(input_query)
            if schema_type == "object":
                result: Dict[str, Any] = {}
                for prop in schema.properties or []:
                    if not prop.name:
                        continue
                    child_path = f"{field_path}.{prop.name}" if field_path else prop.name
                    result[prop.name] = _build_json_value(prop, child_path)
                return result
            if schema_type == "array":
                return []
            return None

        def _build_markdown_content() -> str:
            try:
                parsed_input = ast.literal_eval(input_query)
            except (ValueError, SyntaxError):
                parsed_input = None

            if isinstance(parsed_input, list) and all(isinstance(item, dict) for item in parsed_input):
                lines = ["## Analysis"]
                for item in parsed_input:
                    product_name = item.get("product_name") or "Unknown Product"
                    score = item.get("score") or "N/A"
                    lines.append(f"- {product_name}: score {score}")
                return "\n".join(lines)

            return f"## Analysis\n**Echo:** {input_query[::-1]}"
        
        async def _generate_text_or_markdown() -> Dict[str, Any]:
            """文本/Markdown 模式：单字段流式"""
            if not outputs_schema:
                primary_key = "text" # Fallback
            else:
                primary_key = outputs_schema[0].name
            
            # 模拟 Markdown 内容
            is_md = response_format == "markdown"
            content = _build_markdown_content() if is_md else f"Echo: {input_query[::-1]}"
            
            full_content = ""
            for char in content:
                await asyncio.sleep(0.05)
                if broadcaster:
                    # 广播结构：{ primary_key: char }
                    await broadcaster.broadcast({primary_key: char})
                full_content += char
            
            # 最终结果需要包含其他非主要字段的默认值(如果有)
            base_output = await schemas2obj(outputs_schema, self.context.variables)
            base_output[primary_key] = full_content
            return base_output

        async def _generate_json() -> Dict[str, Any]:
            """JSON 模式：多字段结构化流式"""
            final_data: Dict[str, Any] = {}
            mock_values: Dict[str, str] = {}

            for schema in outputs_schema:
                if not schema.name:
                    continue
                if schema.type == "string":
                    final_data[schema.name] = ""
                    mock_values[schema.name] = _build_json_value(schema, schema.name)
                else:
                    final_data[schema.name] = _build_json_value(schema, schema.name)

            # 2. 模拟逐个字段生成 (或者交替生成)
            # 假设我们只对 String 类型的字段进行流式模拟
            target_fields = [f for f in outputs_schema if f.type == 'string']
            
            if not target_fields:
                # 如果没有字符串字段，直接返回结果，不流式广播
                return final_data

            # 模拟流式过程：我们简单地轮流发送每个字段的一个字符
            # 这模拟了 LLM 在生成 JSON 字符串时的增量解析效果
            max_len = max(len(v) for v in mock_values.values())
            
            for i in range(max_len):
                await asyncio.sleep(0.05)
                chunk_delta = {}
                
                for field in target_fields:
                    val = mock_values[field.name]
                    if i < len(val):
                        char = val[i]
                        chunk_delta[field.name] = char
                        # 更新最终结果
                        current_val = final_data.get(field.name, "")
                        final_data[field.name] = current_val + char
                
                if broadcaster and chunk_delta:
                    # 广播包含多个字段 Delta 的字典
                    await broadcaster.broadcast(chunk_delta)

            return final_data

        # 4. 调度任务
        generator_func = _generate_json if response_format == "json" else _generate_text_or_markdown
        
        if broadcaster:
            task = broadcaster.create_task(generator_func())
            return NodeExecutionResult(input=node_input, data=broadcaster)
        else:
            # 非流式直接运行并返回
            output = await generator_func()
            return NodeExecutionResult(input=node_input, data=NodeResultData(output=output))

# 2. Fail Node (用于测试容错)
FAIL_TEMPLATE = NodeTemplate(
    category=NodeCategory.COMMON,
    icon="play",
    data=NodeData(
        registryId="FailNode",
        name="FailNode",
        description="FailNode",
        inputs=[],
        outputs=[]
    )
)

@register_node(template=FAIL_TEMPLATE)
class FailNode(BaseNode):
    async def execute(self) -> NodeExecutionResult:
        # 总是抛出异常
        raise ValueError("Intentional Failure for Testing")

UNSTABLEWORKER_TEMPLATE = NodeTemplate(
    category=NodeCategory.COMMON,
    icon="play",
    data=NodeData(
        registryId="UnstableWorker",
        name="UnstableWorker",
        description="UnstableWorker",
        inputs=[],
        outputs=[]
    )
)

@register_node(template=UNSTABLEWORKER_TEMPLATE)
class UnstableWorkerNode(BaseNode):
    """
    模拟一个不稳定的处理节点。
    如果输入包含 'Buggy'，则抛出异常，用于测试 executionPolicy。
    如果成功，返回结构化数据。
    """
    async def execute(self) -> NodeExecutionResult:
        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        raw_item = node_input.get("item", "")
        
        # 模拟故障：遇到 "Buggy" 必挂
        if "Buggy" in str(raw_item):
            print(f"    [UnstableWorker] Encountered 'Buggy' item. Simulating Crash...")
            raise ValueError(f"Simulated API Failure for item: {raw_item}")
            
        # 正常处理
        return NodeExecutionResult(input=node_input, data=NodeResultData(output={
            "processed_item": f"SAFE_{raw_item}",
            "status": "ok"
        }))

# 3. Callbacks (用于观察执行过程)
class TestCallbacks:
    async def on_execution_start(self, workflow_def: WorkflowGraphDef) -> None:
        print(f"\n>>> Workflow Started")

    async def on_node_start(self, state: NodeState) -> None:
        print(f"[Node Start] {state.node_id}")

    async def on_node_finish(self, state: NodeState) -> None:
        # result 包含 node_id, status, result 等
        print(f"[Node Finish] {state.node_id} Status: {state.status} Result: {state.model_dump_json()}")

    async def on_stream_chunk(self, event: StreamEvent) -> None:
        # End 节点产生的最终流
        print(f"[Stream] {event.content}", end="", flush=True)

    async def on_node_skipped(self, state: NodeState) -> None:
        print(f"[Node Skipped] {state.node_id}")
        
    async def on_node_error(self, state: NodeState) -> None:
        print(f"[Node Error] {state.node_id}: {state.result.error_msg}")

    async def on_execution_end(self, result: NodeResultData) -> None:
        print(f"\n>>> Workflow Ended. Result: {result.model_dump_json()}\n")
    
    async def on_event(self, type: str, data: Any) -> None:
        pass # Ignore others
