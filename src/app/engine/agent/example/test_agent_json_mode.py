# src/app/engine/agent/example/test_agent_prompt_json.py

import os
import asyncio
import json
import re
from typing import List, Dict, Any, Optional
from app.engine.agent import (
    AgentEngineService,
    AgentInput,
    AgentStep,
    AgentResult,
    BaseToolExecutor,
    AgentEngineCallbacks
)
from app.engine.model.llm import (
    LLMEngineService,
    LLMProviderConfig,
    LLMRunConfig,
    LLMMessage,
    LLMTool,
    LLMToolCall,
    LLMUsage
)

# --- Configuration ---
API_KEY = "sk-LAdEXTUw5P" 
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-plus-2025-09-11"

PROVIDER_CONFIG = LLMProviderConfig(
    client_name="openai", 
    base_url=BASE_URL, 
    api_key=API_KEY
)

# --- Mock Tool ---

class StockInfoTool(BaseToolExecutor):
    """
    模拟股票查询工具。
    """
    async def execute(self, tool_name: str, tool_args: dict) -> dict:
        symbol = tool_args.get("symbol", "").upper()
        print(f"\n    [Tool] 查询股票: {symbol}")
        
        # 模拟返回硬编码数据
        mock_db = {
            "BABA": {"price": 85.5, "currency": "USD", "change": "+1.2%"},
            "AAPL": {"price": 175.0, "currency": "USD", "change": "-0.5%"},
            "TENCENT": {"price": 300.0, "currency": "HKD", "change": "+2.0%"}
        }
        
        result = mock_db.get(symbol)
        if result:
            return result
        return {"error": "Stock symbol not found"}

    def get_llm_tools(self) -> List[LLMTool]:
        return [
            LLMTool(function={
                "name": "get_stock_price",
                "description": "获取股票的当前价格信息",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "股票代码，如 BABA, AAPL"}
                    },
                    "required": ["symbol"]
                }
            })
        ]

class SimpleCallback(AgentEngineCallbacks):
    async def on_agent_start(self) -> None:
        pass # 保持输出简洁
    
    async def on_agent_step(self, step: AgentStep) -> None:
        print(f"  > [Agent 步骤] 调用工具: {step.action.function['name']}")

    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]) -> None:
        pass
    async def on_final_chunk_generated(self, chunk: str) -> None:
        pass
    async def on_agent_finish(self, result: AgentResult) -> None:
        pass
    async def on_agent_cancel(self, result: AgentResult) -> None:
        pass
    async def on_agent_error(self, error: Exception) -> None:
        print(f"Agent Error: {error}")
    async def on_usage(self, usage: LLMUsage) -> None:
        pass

# --- Helper Function ---

def extract_json_from_text(text: str) -> Optional[Dict]:
    """
    从模型输出中提取 JSON。
    模型通过 Prompt 生成 JSON 时，经常会包含 Markdown 标记（```json ... ```）。
    此函数负责清洗这些标记。
    """
    # 1. 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 2. 尝试提取代码块 ```json ... ```
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, text)
    if match:
        json_str = match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
            
    # 3. 尝试寻找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        json_str = text[start : end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
            
    return None

# --- Test Case ---

async def test_prompt_driven_json():
    print("\n" + "="*80)
    print(">>> Test: Prompt-Driven JSON Output (Without Native JSON Mode) <<<")
    print("="*80)

    llm_engine = LLMEngineService()
    agent_engine = AgentEngineService(llm_engine=llm_engine)
    tool_executor = StockInfoTool()
    
    # 1. 配置: 关键点 —— response_format=None
    # 让 Agent 像普通聊天一样自由运行，这样它就会正常调用工具。
    run_config = LLMRunConfig(
        model=MODEL_NAME,
        tools=tool_executor.get_llm_tools(),
        temperature=0.1, 
        response_format=None # 显式关闭 JSON 模式
    )

    # 2. Prompt: 显式要求 JSON
    system_prompt = """你是一个金融数据助手。
    
    任务：
    1. 接收用户的股票查询请求。
    2. 使用工具查询数据。
    3. 最终输出必须是严格的 JSON 格式。
    
    输出要求：
    - 不要输出任何闲聊、问候或多余的解释性文字。
    - 仅输出 JSON 对象。
    - 确保 JSON 可以被 Python 的 json.loads 解析。
    
    JSON 结构示例：
    {
        "portfolio": [
            {"symbol": "股票代码", "price": 数字, "currency": "货币"}
        ],
        "total_valuation_usd": 数字,
        "summary": "简短的一句话总结"
    }
    """

    user_message = "请帮我查一下 BABA 和 AAPL 的价格，并生成报告。"
    
    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message)
    ]

    print(f"  [Input] 用户请求: {user_message}")

    try:
        result: AgentResult = await agent_engine.run(
            agent_input=AgentInput(messages=messages),
            provider_config=PROVIDER_CONFIG,
            run_config=run_config,
            callbacks=SimpleCallback(),
            tool_executor=tool_executor
        )

        print("-" * 50)
        print(f"  [Agent Raw Output]\n{result.message.content}")
        print("-" * 50)

        # 3. 验证步骤：检查是否使用了工具
        # 在普通模式下，Agent 应该先进行工具调用，然后才生成最终回答
        if len(result.steps) >= 2:
            print(f"  ✓ 成功: Agent 执行了 {len(result.steps)} 步 (预期行为)")
        else:
            print(f"  ⚠ 警告: 步骤数较少 ({len(result.steps)})，可能未完全调用所有工具")

        # 4. 验证格式：尝试提取并解析 JSON
        json_data = extract_json_from_text(result.message.content)
        
        if json_data:
            print("  ✓ 成功: 输出包含有效的 JSON 数据")
            print(f"  [Parsed Data] {json.dumps(json_data, indent=2, ensure_ascii=False)}")
            
            # 业务逻辑验证
            portfolio = json_data.get("portfolio", [])
            symbols = [item.get("symbol") for item in portfolio]
            if "BABA" in symbols and "AAPL" in symbols:
                print("  ✓ 数据验证: 包含了请求的所有股票")
            else:
                print("  ✗ 数据验证: 缺少部分股票数据")
        else:
            print("  ✗ 失败: 无法从输出中提取 JSON")

    except Exception as e:
        print(f"测试异常: {e}")

async def main():
    await test_prompt_driven_json()

if __name__ == "__main__":
    asyncio.run(main())