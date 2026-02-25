import asyncio
import json
from ..main import WorkflowEngineService
from .mocks import TestCallbacks, MockLLMNode, FailNode

async def run_basic_flow():
    print("=== Test 1: Basic Linear Flow (Start -> Output -> End) ===")
    # 修正点：模板变量 {{name}} 对应 inputs 中的 name
    
    workflow = {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "outputs": [{"name": "name", "type": "string"}]
                }
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "config": {
                        "returnType": "Text",
                        "content": "Hello {{name}}!" # [Fix] 使用本地 input 变量名
                    },
                    "inputs": [
                        {
                            "name": "name", 
                            "type": "string", 
                            "value": {"type": "ref", "content": {"blockID": "start", "path": "name"}} 
                            # Start 节点通常比较特殊，它的输出直接挂在根路径或由 payload 决定。
                            # 假设 payload={"name": "Prisma"}，Start output={"name": "Prisma"}
                        }
                    ]
                }
            }
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"}
        ]
    }
    
    engine = WorkflowEngineService()
    await engine.run(workflow, payload={"name": "PrismaSpace"}, callbacks=TestCallbacks())


async def run_streaming_flow():
    print("=== Test 2: Streaming Flow (Start -> MockLLM -> End) ===")
    # 修正点1：模板变量 {{text}} 对应 inputs 中的 text
    # 修正点2：引用路径改为 output.text，因为 NodeExecutionResult 默认包裹在 output 中
    
    workflow = {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "outputs": [{"name": "topic", "type": "string"}]
                }
            },
            {
                "id": "llm",
                "data": {
                    "registryId": "MockLLM",
                    "name": "LLM Generator",
                    "config": {
                        "model": "gpt-4",
                    },
                    "inputs": [
                        {"name": "input_query", "type": "string", "value": {"type": "ref", "content": {"blockID": "start", "path": "topic"}}}
                    ],
                    "outputs": [
                        {"name": "text", "type": "string"}
                    ]
                }
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "config": {
                        "returnType": "Text",
                        "stream": True,
                        "content": "Result: {{text}}" # [Fix] 本地变量名
                    },
                    "inputs": [
                        # [Fix] path="output.text" 对应 MockLLM 返回的 {"output": {"text": ...}}
                        {"name": "text", "type": "string", "value": {"type": "ref", "content": {"blockID": "llm", "path": "text"}}}
                    ]
                }
            }
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "llm", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "llm", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"}
        ]
    }
    
    engine = WorkflowEngineService()
    
    await engine.run(workflow, payload={"topic": "12345"}, callbacks=TestCallbacks())


async def run_markdown_flow():
    print("\n=== Test 3: Markdown Output Flow ===")
    # Start -> MockLLM (Markdown Mode) -> End
    
    workflow = {
        "nodes": [
            {"id": "start", "data": {"registryId": "Start", "name": "Start", "outputs": [{"name": "input", "type": "string"}]}},
            {
                "id": "llm",
                "data": {
                    "registryId": "MockLLM",
                    "name": "LLM MD",
                    "config": {
                        "model": "gpt-4",
                        "response_format": "markdown", # [Config] 启用 Markdown
                    },
                    "inputs": [
                        {"name": "input_query", "type": "string", "value": {"type": "ref", "content": {"blockID": "start", "path": "input"}}}
                    ],
                    "outputs": [
                        {"name": "md_content", "type": "string"} # 自定义变量名
                    ]
                }
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "config": {
                        "returnType": "Text",
                        "stream": True,
                        "content": "Rendered:\n{{content}}"
                    },
                    "inputs": [
                        {"name": "content", "type": "string", "value": {"type": "ref", "content": {"blockID": "llm", "path": "md_content"}}}
                    ]
                }
            }
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "llm", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "llm", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"}
        ]
    }
    
    engine = WorkflowEngineService()
    await engine.run(workflow, payload={"input": "CBA"}, callbacks=TestCallbacks())


async def run_json_flow():
    print("\n=== Test 4: JSON Mode Flow (Structured Output) ===")
    # Start -> MockLLM (JSON Mode with 2 fields) -> End
    # 验证 MockLLM 能同时流式传输两个字段：reasoning 和 answer
    
    workflow = {
        "nodes": [
            {"id": "start", "data": {"registryId": "Start", "name": "Start", "outputs": [{"name": "q", "type": "string"}]}},
            {
                "id": "llm",
                "data": {
                    "registryId": "MockLLM",
                    "name": "LLM JSON",
                    "config": {
                        "model": "gpt-4",
                        "response_format": "json", # [Config] 启用 JSON 模式
                    },
                    "inputs": [
                        {"name": "input_query", "type": "string", "value": {"type": "ref", "content": {"blockID": "start", "path": "q"}}}
                    ],
                    "outputs": [
                        # 定义两个输出字段
                        {"name": "reasoning", "type": "string"},
                        {"name": "answer", "type": "string"}
                    ]
                }
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "config": {
                        "returnType": "Text",
                        "stream": True,
                        # 同时引用两个字段，验证流式拼接能力
                        "content": "Think: {{reason}}\nResult: {{ans}}"
                    },
                    "inputs": [
                        {"name": "reason", "type": "string", "value": {"type": "ref", "content": {"blockID": "llm", "path": "reasoning"}}},
                        {"name": "ans", "type": "string", "value": {"type": "ref", "content": {"blockID": "llm", "path": "answer"}}}
                    ]
                }
            }
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "llm", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "llm", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"}
        ]
    }
    
    engine = WorkflowEngineService()
    await engine.run(workflow, payload={"q": "Why?"}, callbacks=TestCallbacks())

async def run_fault_tolerance_flow():
    print("=== Test 5: Fault Tolerance (Retry & Fallback) ===")
    
    workflow = {
        "nodes": [
            {
                "id": "start", 
                "data": {"registryId": "Start", "name": "Start", "outputs": []}
            },
            {
                "id": "risky",
                "data": {
                    "registryId": "FailNode",
                    "name": "Risky Node",
                    "config": {
                        "executionPolicy": {
                            "switch": True,
                            "retryTimes": 2,
                            "processType": 2, # Fallback
                            "dataOnErr": "SafeValue"
                        }
                    },
                    "outputs": [{"name": "val", "type": "string"}]
                }
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "config": {
                        "returnType": "Text", 
                        # [验证点] 引用 runtimeStatus 中的降级数据
                        "content": "ErrorData: {{errData}}"
                    }, 
                    "inputs": [
                         {
                             "name": "errData", 
                             "type": "string", 
                             # 引用路径：output -> runtimeStatus -> errorBody -> data
                             "value": {"type": "ref", "content": {"blockID": "risky", "path": "runtimeStatus.errorBody.data"}}
                         }
                    ]
                }
            }
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "risky", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "risky", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"}
        ]
    }
    
    engine = WorkflowEngineService()
    await engine.run(workflow, payload={}, callbacks=TestCallbacks())


async def run_invalid_flow():
    print("=== Test 6: Invalid Workflow (Cycle) ===")
    workflow = {
        "nodes": [
            {"id": "A", "data": {"registryId": "Start", "name": "A"}},
            {"id": "B", "data": {"registryId": "End", "name": "B"}}
        ],
        "edges": [
            {"sourceNodeID": "A", "targetNodeID": "B", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "B", "targetNodeID": "A", "sourcePortID": "0", "targetPortID": "0"}
        ]
    }
    
    engine = WorkflowEngineService()
    try:
        await engine.run(workflow, payload={})
    except ValueError as e:
        print(f"Caught expected error: {e}")

async def run_complex_flow():
    print("\n=== Test 7: Complex Flow (Loop + LLM Aggregation) ===")
    
    workflow = {
        "nodes": [
            # 1. Start
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "outputs": [{"name": "list", "type": "array", "items": {
                                "type": "string",
                            }, "value": {"type": "literal", "content": ["short", "long_text"]}}]
                }
            },
            # 2. Loop
            {
                "id": "loop",
                "data": {
                    "registryId": "Loop",
                    "name": "Loop",
                    "config": {
                        "loopType": "list",
                        "loopList": {
                            "name": "loopList",
                            "type": "array",
                            "items": {
                                "type": "string",
                            },
                            "value": {
                                "type": "ref", 
                                "content": {"blockID": "start", "path": "list"}
                            }
                        }
                    },
                    "inputs": [],
                    "outputs": [
                        {
                            "name": "results",
                            "type": "array",
                            "items": {
                                "type": "string",
                            },
                            "value": {
                                "type": "ref",
                                "content": {
                                    "source": "loop-block-output", 
                                    "blockID": "llm",  # 指向子节点
                                    "path": "res"      # 子节点输出路径
                                }
                            }
                        }
                    ],
                    "blocks": [
                        {
                            "id": "llm",
                            "data": {
                                "registryId": "MockLLM",
                                "name": "LLM",
                                "config": {
                                    "model": "gpt-4",
                                },
                                "inputs": [{"name": "input_query", "type": "string", "value": {"type": "ref", "content": {"blockID": "loop", "path": "item"}}}],
                                "outputs": [{"name": "res", "type": "string"}]
                            }
                        }
                    ],
                    "edges": [
                        # 内部连线主要用于控制流，数据流走 Context 引用
                        {"sourceNodeID": "loop", "targetNodeID": "llm", "sourcePortID": "loop-function-inline-output", "targetPortID": "0"},
                        {"sourceNodeID": "llm", "targetNodeID": "loop", "sourcePortID": "0", "targetPortID": "loop-function-inline-input"}
                    ]
                }
            },
            # 3. End
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "config": {"returnType": "Object"},
                    "inputs": [{"name": "final", "type": "array", "items": {
                                "type": "string",
                            }, "value": {"type": "ref", "content": {"blockID": "loop", "path": "results"}}}]
                }
            }
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "loop", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "loop", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"}
        ]
    }

    engine = WorkflowEngineService()
    await engine.run(workflow, payload={}, callbacks=TestCallbacks())

async def main():
    await run_basic_flow()
    await run_streaming_flow()
    await run_markdown_flow()
    await run_json_flow()
    await run_fault_tolerance_flow()
    await run_invalid_flow()
    await run_complex_flow()
    
if __name__ == "__main__":
    asyncio.run(main())