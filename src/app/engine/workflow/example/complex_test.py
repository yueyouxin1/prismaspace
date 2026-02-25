import asyncio
import json
from typing import Dict, Any, List

# 引入之前的定义
from ..main import WorkflowEngineService
from .mocks import TestCallbacks, MockLLMNode, FailNode, UnstableWorkerNode
from ...utils.parameter_schema_utils import schemas2obj

async def run_complex_system_test():
    print("\n" + "="*80)
    print("=== FINAL EXAM: Complex System Test (Loop + Fault Tolerance + Branch + Stream) ===")
    print("Scenario: Analyze a list of products. One product causes a crash, but the system should recover.")
    print("="*80 + "\n")

    # -------------------------------------------------------------------------
    # 数据流设计：
    # 1. Start: 输入产品列表 (包含一个 "Buggy Product" 触发故障)
    # 2. Loop (Analyzer): 
    #    - Step A (UnstableWorker): 尝试预处理。遇到 Buggy 会失败，策略是重试1次后降级。
    #    - Step B (MockLLM - JSON): 对预处理结果进行评分。
    #    - Aggregation: 收集所有评分结果。
    # 3. Output: 打印中间聚合结果。
    # 4. Branch: 检查是否需要生成详细报告 (generate_report=True)。
    # 5. Final LLM (Markdown): 如果需要报告，将聚合结果生成 Markdown 摘要。
    # 6. End: 流式输出最终报告。
    # -------------------------------------------------------------------------

    workflow = {
        "nodes": [
            # --- 1. Start Node ---
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "User Input",
                    "outputs": [
                        # 复杂数组定义：Array<String>
                        {
                            "name": "products",
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        # 控制开关
                        {"name": "generate_report", "type": "boolean"}
                    ]
                }
            },

            # --- 2. Loop Node (The Engine Room) ---
            {
                "id": "analyzer_loop",
                "data": {
                    "registryId": "Loop",
                    "name": "Batch Analysis",
                    "config": {
                        "loopType": "list",
                        "loopList": {
                            "name": "list_ref",
                            "type": "array",
                            "items": {"type": "string"},
                            "value": {"type": "ref", "content": {"blockID": "start", "path": "products"}}
                        },
                        "loopCount": None
                    },
                    "inputs": [], # 不需要额外外部变量，使用 loopList 的 item
                    
                    # 定义循环对外输出：聚合内部 LLM 的结果
                    "outputs": [
                        {
                            "name": "batch_results",
                            "type": "array",
                            "items": { 
                                "type": "object", 
                                "properties": [
                                    {"name": "product_name", "type": "string"},
                                    {"name": "score", "type": "string"}
                                ]
                            },
                            "value": {
                                "type": "ref",
                                "content": {
                                    "source": "loop-block-output",
                                    "blockID": "inner_llm", # 聚合内部 inner_llm 的输出
                                    "path": "analysis_json" # 聚合其 output.analysis_json 字段
                                }
                            }
                        }
                    ],
                    
                    # --- Loop 内部子图 ---
                    "blocks": [
                        # Sub-Node A: 不稳定的预处理器
                        {
                            "id": "worker",
                            "data": {
                                "registryId": "UnstableWorker", 
                                "name": "Unstable Preprocessor",
                                "config": {
                                    # [核心测试点] 容错策略
                                    "executionPolicy": {
                                        "switch": True,
                                        "retryTimes": 1,       # 失败重试1次
                                        "processType": 2,      # 2 = 降级 (并不中断循环)
                                        "dataOnErr": "fallback_val" # 引擎暂未自动映射到output，但会标记为完成
                                    }
                                },
                                "inputs": [
                                    {"name": "item", "type": "string", "value": {"type": "ref", "content": {"blockID": "analyzer_loop", "path": "item"}}}
                                ],
                                "outputs": [{"name": "processed_item", "type": "string"}]
                            }
                        },
                        # Sub-Node B: 内部评分 LLM
                        {
                            "id": "inner_llm",
                            "data": {
                                "registryId": "MockLLM",
                                "name": "Score Rater",
                                "config": {"model": "gpt-4", "system_prompt": "", "response_format": "json"}, # 要求 JSON 结构化输出
                                "inputs": [
                                    # 引用上一个节点。如果上一个节点失败降级，这里引用可能拿到 None，
                                    # 或者我们可以引用 runtimeStatus 做更高级处理。
                                    # 这里简单引用 output，MockLLM 会处理 None 为 "parsed..."
                                    {"name": "input_query", "type": "string", "value": {"type": "ref", "content": {"blockID": "worker", "path": "processed_item"}}}
                                ],
                                "outputs": [
                                    # MockLLM 在 JSON 模式下会流式生成这个字段
                                    {"name": "analysis_json", "type": "object"} 
                                ]
                            }
                        }
                    ],
                    # 子图连线
                    "edges": [
                        {"sourceNodeID": "analyzer_loop", "targetNodeID": "worker", "sourcePortID": "loop-function-inline-output", "targetPortID": "0"},
                        {"sourceNodeID": "worker", "targetNodeID": "inner_llm", "sourcePortID": "0", "targetPortID": "0"},
                        {"sourceNodeID": "inner_llm", "targetNodeID": "analyzer_loop", "sourcePortID": "0", "targetPortID": "loop-function-inline-input"}
                    ]
                }
            },

            # --- 3. Output Node (Debugging) ---
            {
                "id": "debug_output",
                "data": {
                    "registryId": "Output",
                    "name": "Debug Log",
                    "outputs": [{"name": "raw_data", "type": "array", "items": {"type":"object"}, "value": {"type": "ref", "content": {"blockID": "analyzer_loop", "path": "batch_results"}}}]
                }
            },

            # --- 4. Branch Node ---
            {
                "id": "gatekeeper",
                "data": {
                    "registryId": "Branch",
                    "name": "Check Mode",
                    "config": {
                        "branchs": [
                            {
                                "id": "yes_report",
                                "conditions": [
                                    # 检查 start.generate_report == True
                                    {
                                        "operator": 1, # Equal
                                        "left": {"name": "L", "type": "boolean", "value": {"type": "ref", "content": {"blockID": "start", "path": "generate_report"}}},
                                        "right": {"name": "R", "type": "boolean", "value": {"type": "literal", "content": True}}
                                    }
                                ]
                            }
                        ]
                    }
                }
            },

            # --- 5. Final Summary LLM (Stream Producer) ---
            {
                "id": "final_writer",
                "data": {
                    "registryId": "MockLLM",
                    "name": "Report Writer",
                    "config": {"model": "gpt-4", "response_format": "markdown"}, # Markdown 流式模式
                    "inputs": [
                        # 将数组转为字符串给 LLM
                        {"name": "input_query", "type": "string", "value": {"type": "ref", "content": {"blockID": "analyzer_loop", "path": "batch_results"}}}
                    ],
                    "outputs": [
                        {"name": "report_md", "type": "string"}
                    ]
                }
            },

            # --- 6. End Node ---
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End Stream",
                    "config": {
                        "returnType": "Text",
                        "stream": True, # [核心] 开启流式响应
                        "content": "# Final Report\n{{report_content}}"
                    },
                    "inputs": [
                        {"name": "report_content", "type": "string", "value": {"type": "ref", "content": {"blockID": "final_writer", "path": "report_md"}}}
                    ]
                }
            }
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "analyzer_loop", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "analyzer_loop", "targetNodeID": "debug_output", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "debug_output", "targetNodeID": "gatekeeper", "sourcePortID": "0", "targetPortID": "0"},
            # 分支逻辑：端口 "0" (第一个分支) 激活 -> Final Writer
            {"sourceNodeID": "gatekeeper", "targetNodeID": "final_writer", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "final_writer", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"}
            # 注意：如果分支不匹配，流程会自然结束，或者我们可以连一个 "Else" 节点。
            # 这里我们保证测试数据 inputs 会激活该分支。
        ]
    }

    # 准备测试数据
    # "Buggy Product" 将触发 UnstableWorker 的重试和降级逻辑
    params = {
        "products": ["MacBook Pro", "Buggy Product", "iPhone 15"],
        "generate_report": True 
    }

    engine = WorkflowEngineService()

    try:
        await engine.run(workflow, payload=params, callbacks=TestCallbacks())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Test Failed with error: {e}")
        raise e

# 运行入口
if __name__ == "__main__":
    asyncio.run(run_complex_system_test())