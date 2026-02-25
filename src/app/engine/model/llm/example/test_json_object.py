# src/app/engine/model/llm/example/test_json_object.py

import os
import asyncio
import json
from app.engine.model.llm import (
    LLMEngineService,
    LLMProviderConfig,
    LLMRunConfig,
    LLMMessage,
    LLMResult
)
from ._callbacks import PrintCallbacks

# --- Configuration ---
API_KEY = "sk-LAdEXTUw5P"
if not API_KEY:
    raise ValueError("API_KEY environment variable not set.")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-plus-2025-09-11"

# JSON Object 模式配置
PROVIDER_CONFIG = LLMProviderConfig(
    client_name="openai", 
    base_url=BASE_URL, 
    api_key=API_KEY
)

RUN_CONFIG_JSON_OBJECT = LLMRunConfig(
    model=MODEL_NAME,
    stream=True,  # 结构化输出通常不需要流式
    temperature=0.1,  # 降低温度以获得更确定性的输出
    response_format={"type": "json_object"}
)
# ---------------------

async def test_json_object():
    """测试 JSON Object 模式的结构化输出"""
    print(">>> Testing JSON Object Mode <<<")
    
    # 1. 初始化引擎和回调
    engine = LLMEngineService()
    callbacks = PrintCallbacks()
    
    # 2. 定义包含 JSON 关键词的消息（必须包含 "json" 关键词）
    messages = [
        LLMMessage(
            role="system", 
            content="""你是一个数据提取助手。请将用户提供的信息提取为JSON格式。
            输出必须是有效的JSON对象，包含以下字段：
            - "answer": 直接回答用户的问题
            - "explanation": 提供简要解释
            - "confidence": 0到1之间的置信度评分
            记住：输出必须是JSON格式。"""
        ),
        LLMMessage(
            role="user", 
            content="比较9.11和9.9的大小，并告诉我哪个更大。"
        ),
    ]
    
    # 3. 运行引擎
    try:
        result: LLMResult = await engine.run(
            provider_config=PROVIDER_CONFIG,
            run_config=RUN_CONFIG_JSON_OBJECT,
            messages=messages,
            callbacks=callbacks
        )
        
        # 4. 验证输出是否为有效的 JSON
        if result:
            print(f"\n原始输出: {result.message.content}")
            
            try:
                json_data = json.loads(result.message.content)
                print(f"解析为JSON: {json.dumps(json_data, ensure_ascii=False, indent=2)}")
                
                # 验证基本结构
                required_keys = ["answer", "explanation", "confidence"]
                missing_keys = [key for key in required_keys if key not in json_data]
                
                if missing_keys:
                    print(f"警告: 缺少必要的键: {missing_keys}")
                else:
                    print("✓ JSON 结构验证通过")
                    
            except json.JSONDecodeError as e:
                print(f"✗ 输出不是有效的 JSON: {e}")
                
        print(f"Tokens 使用情况: {result.usage}")
        
    except Exception as e:
        print(f"\n测试过程中发生异常: {e}")
        import traceback
        traceback.print_exc()

async def test_json_object_with_different_prompts():
    """测试不同提示词对 JSON Object 模式的影响"""
    print("\n>>> Testing JSON Object with Different Prompts <<<")
    
    engine = LLMEngineService()
    
    test_cases = [
        {
            "name": "简单JSON输出",
            "system": "输出必须是JSON格式。",
            "user": "告诉我北京和上海的人口，用JSON格式回复。"
        },
        {
            "name": "复杂数据结构",
            "system": "你是一个天气API。总是以JSON格式回复，包含city, temperature, condition, humidity字段。",
            "user": "今天北京的天气如何？"
        }
    ]
    
    for test_case in test_cases:
        print(f"\n--- 测试: {test_case['name']} ---")
        
        messages = [
            LLMMessage(role="system", content=test_case["system"]),
            LLMMessage(role="user", content=test_case["user"])
        ]
        
        try:
            result = await engine.run(
                provider_config=PROVIDER_CONFIG,
                run_config=RUN_CONFIG_JSON_OBJECT,
                messages=messages,
                callbacks=None
            )

            if result:
            
                print(f"输出: {result.message.content[:200]}...")  # 只显示前200字符
                
                # 尝试解析JSON
                try:
                    json.loads(result.message.content)
                    print("✓ 有效的JSON")
                except:
                    print("✗ 无效的JSON")
                
        except Exception as e:
            print(f"错误: {e}")

async def main():
    await test_json_object()
    await test_json_object_with_different_prompts()

if __name__ == "__main__":
    print("=" * 60)
    print("JSON Object 模式测试")
    print("=" * 60)
    
    # 运行测试
    asyncio.run(main())