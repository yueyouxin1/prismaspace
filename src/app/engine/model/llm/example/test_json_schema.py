# src/app/engine/model/llm/example/test_json_schema.py

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

PROVIDER_CONFIG = LLMProviderConfig(
    client_name="openai", 
    base_url=BASE_URL, 
    api_key=API_KEY
)

# 定义 JSON Schema - 根据阿里云官方文档格式
MATH_COMPARISON_SCHEMA = {
    "type": "object",
    "properties": {
        "larger_number": {
            "type": "number",
            "description": "较大的数字"
        },
        "smaller_number": {
            "type": "number",
            "description": "较小的数字"
        },
        "difference": {
            "type": "number",
            "description": "两个数字的差值"
        },
        "explanation": {
            "type": "string",
            "description": "比较过程的解释"
        },
        "is_significant": {
            "type": "boolean",
            "description": "差异是否显著"
        }
    },
    "required": ["larger_number", "smaller_number", "difference", "explanation", "is_significant"],
    "additionalProperties": False
}

# 根据阿里云官方示例格式
RUN_CONFIG_MATH = LLMRunConfig(
    model=MODEL_NAME,
    stream=True,
    temperature=0.1,
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "number_comparison_schema",
            "strict": True,
            "schema": MATH_COMPARISON_SCHEMA
        }
    }
)

# 第二个测试用例的 Schema
PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price": {"type": "number"},
                    "category": {"type": "string"},
                    "in_stock": {"type": "boolean"}
                },
                "required": ["name", "price", "category", "in_stock"]
            }
        },
        "total_value": {"type": "number"},
        "most_expensive": {"type": "string"}
    },
    "required": ["products", "total_value", "most_expensive"],
    "additionalProperties": False
}

RUN_CONFIG_PRODUCT = LLMRunConfig(
    model=MODEL_NAME,
    stream=True,
    temperature=0.1,
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "product_catalog_schema",
            "strict": True,
            "schema": PRODUCT_SCHEMA
        }
    }
)
# ---------------------

async def test_json_schema_math():
    """测试 JSON Schema 模式 - 数学比较"""
    print(">>> Testing JSON Schema Mode - Math Comparison <<<")
    
    engine = LLMEngineService()
    callbacks = PrintCallbacks()
    
    # JSON Schema 模式不需要在提示词中包含 "JSON" 关键词
    messages = [
        LLMMessage(
            role="system", 
            content="你是一个数学比较助手。请比较用户提供的两个数字。"
        ),
        LLMMessage(
            role="user", 
            content="请比较 9.11 和 9.9 这两个数字，告诉我哪个更大，并计算它们的差值。"
        ),
    ]
    
    try:
        result: LLMResult = await engine.run(
            provider_config=PROVIDER_CONFIG,
            run_config=RUN_CONFIG_MATH,
            messages=messages,
            callbacks=callbacks
        )
        
        if result:
            print(f"\n原始输出: {result.message.content}")
            
            try:
                json_data = json.loads(result.message.content)
                print(f"解析为JSON:\n{json.dumps(json_data, ensure_ascii=False, indent=2)}")
                
                # 验证是否符合 schema
                validation_result = validate_against_schema(json_data, MATH_COMPARISON_SCHEMA)
                if validation_result["valid"]:
                    print("✓ 符合 JSON Schema")
                    
                    # 逻辑验证
                    if json_data["larger_number"] == 9.11 and json_data["smaller_number"] == 9.9:
                        print("✓ 数字比较正确")
                    if abs(json_data["difference"] - 0.21) < 0.01:
                        print("✓ 差值计算正确")
                        
                else:
                    print(f"✗ 不符合 JSON Schema: {validation_result['errors']}")
                    
            except json.JSONDecodeError as e:
                print(f"✗ 输出不是有效的 JSON: {e}")
                
        print(f"Tokens 使用情况: {result.usage}")
        
    except Exception as e:
        print(f"\n测试过程中发生异常: {e}")

async def test_json_schema_product_catalog():
    """测试 JSON Schema 模式 - 产品目录"""
    print("\n>>> Testing JSON Schema Mode - Product Catalog <<<")
    
    engine = LLMEngineService()
    
    messages = [
        LLMMessage(
            role="system", 
            content="你是一个产品目录管理系统。请根据用户描述生成产品列表。"
        ),
        LLMMessage(
            role="user", 
            content="我有以下产品：苹果手机价格8999元，有库存；华为笔记本价格6999元，缺货；小米电视价格3299元，有库存。请整理成产品目录。"
        ),
    ]
    
    try:
        result: LLMResult = await engine.run(
            provider_config=PROVIDER_CONFIG,
            run_config=RUN_CONFIG_PRODUCT,
            messages=messages,
            callbacks=None
        )
        
        if result:
            print(f"\n原始输出: {result.message.content}")
            
            try:
                json_data = json.loads(result.message.content)
                print(f"解析为JSON:\n{json.dumps(json_data, ensure_ascii=False, indent=2)}")
                
                # 验证是否符合 schema
                validation_result = validate_against_schema(json_data, PRODUCT_SCHEMA)
                if validation_result["valid"]:
                    print("✓ 符合 JSON Schema")
                    
                    # 业务逻辑验证
                    products = json_data.get("products", [])
                    print(f"✓ 找到 {len(products)} 个产品")
                    
                    # 计算总价值验证
                    calculated_total = sum(p.get("price", 0) for p in products)
                    if abs(json_data.get("total_value", 0) - calculated_total) < 0.01:
                        print("✓ 总价值计算正确")
                        
                else:
                    print(f"✗ 不符合 JSON Schema: {validation_result['errors']}")
                    
            except json.JSONDecodeError as e:
                print(f"✗ 输出不是有效的 JSON: {e}")
                
    except Exception as e:
        print(f"\n测试过程中发生异常: {e}")

async def test_json_schema_variations():
    """测试 JSON Schema 的不同变体"""
    print("\n>>> Testing JSON Schema Variations <<<")
    
    engine = LLMEngineService()
    messages = [LLMMessage(role="user", content="9.11和9.9哪个大？")]
    
    variations = [
        {
            "name": "严格模式 (strict=True)",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "strict_comparison",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "larger": {"type": "number"},
                            "smaller": {"type": "number"}
                        },
                        "required": ["larger", "smaller"],
                        "additionalProperties": False
                    }
                }
            }
        },
        {
            "name": "非严格模式 (strict=False)",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "non_strict_comparison",
                    "strict": False,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "larger": {"type": "number"},
                            "smaller": {"type": "number"}
                        },
                        "required": ["larger", "smaller"]
                    }
                }
            }
        },
        {
            "name": "不带 name 字段",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "larger": {"type": "number"},
                            "smaller": {"type": "number"}
                        },
                        "required": ["larger", "smaller"]
                    }
                }
            }
        }
    ]
    
    for variation in variations:
        print(f"\n--- 测试: {variation['name']} ---")
        
        run_config = LLMRunConfig(
            model=MODEL_NAME,
            stream=False,
            temperature=0.1,
            response_format=variation["response_format"]
        )
        
        try:
            result = await engine.run(
                provider_config=PROVIDER_CONFIG,
                run_config=run_config,
                messages=messages,
                callbacks=None
            )
            
            print(f"输出: {result.message.content}")
            
            try:
                json_data = json.loads(result.message.content)
                print(f"JSON 结构: {list(json_data.keys())}")
            except:
                print("输出不是有效的 JSON")
                
        except Exception as e:
            print(f"错误: {e}")

async def test_json_object_comparison():
    """对比测试 JSON Object 模式和 JSON Schema 模式"""
    print("\n>>> Testing JSON Object vs JSON Schema <<<")
    
    engine = LLMEngineService()
    user_message = "比较9.11和9.9的大小，用JSON格式回复，包含larger和smaller字段。"
    
    # JSON Object 模式
    print("\n1. JSON Object 模式:")
    messages_json_object = [
        LLMMessage(role="system", content="请以JSON格式输出。"),
        LLMMessage(role="user", content=user_message)
    ]
    
    run_config_json_object = LLMRunConfig(
        model=MODEL_NAME,
        stream=False,
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    
    try:
        result_json = await engine.run(
            provider_config=PROVIDER_CONFIG,
            run_config=run_config_json_object,
            messages=messages_json_object,
            callbacks=None
        )
        print(f"输出: {result_json}")
    except Exception as e:
        print(f"错误: {e}")
    
    # JSON Schema 模式
    print("\n2. JSON Schema 模式:")
    messages_json_schema = [
        LLMMessage(role="user", content=user_message)
    ]
    
    run_config_json_schema = LLMRunConfig(
        model=MODEL_NAME,
        stream=False,
        temperature=0.1,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "simple_comparison",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "larger": {"type": "number"},
                        "smaller": {"type": "number"}
                    },
                    "required": ["larger", "smaller"],
                    "additionalProperties": False
                }
            }
        }
    )
    
    try:
        result_schema = await engine.run(
            provider_config=PROVIDER_CONFIG,
            run_config=run_config_json_schema,
            messages=messages_json_schema,
            callbacks=None
        )
        print(f"输出: {result_schema}")
    except Exception as e:
        print(f"错误: {e}")

def validate_against_schema(data, schema):
    """简单的 schema 验证函数"""
    errors = []
    
    # 检查必需字段
    if "required" in schema:
        for field in schema["required"]:
            if field not in data:
                errors.append(f"缺少必需字段: {field}")
    
    # 检查不允许的额外字段
    if schema.get("additionalProperties") is False:
        allowed_properties = set(schema.get("properties", {}).keys())
        actual_properties = set(data.keys())
        extra_properties = actual_properties - allowed_properties
        if extra_properties:
            errors.append(f"不允许的额外字段: {extra_properties}")
    
    # 简单的类型检查
    if "properties" in schema:
        for field, field_schema in schema["properties"].items():
            if field in data:
                expected_type = field_schema.get("type")
                if expected_type:
                    actual_type = type(data[field]).__name__
                    type_mapping = {
                        "int": "integer",
                        "float": "number",
                        "str": "string",
                        "bool": "boolean",
                        "list": "array",
                        "dict": "object"
                    }
                    actual_type_normalized = type_mapping.get(actual_type, actual_type)
                    
                    if expected_type == "number" and actual_type in ["int", "float"]:
                        continue  # int 和 float 都是 number
                    elif expected_type == "array" and actual_type == "list":
                        continue  # list 对应 array
                    elif expected_type == "object" and actual_type == "dict":
                        continue  # dict 对应 object
                    elif expected_type != actual_type_normalized:
                        errors.append(f"字段 '{field}' 类型错误: 期望 {expected_type}, 实际 {actual_type_normalized}")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors
    }

async def main():
    await test_json_schema_math()
    await test_json_schema_product_catalog()
    await test_json_schema_variations()
    await test_json_object_comparison()

if __name__ == "__main__":
    print("=" * 60)
    print("JSON Schema 模式测试 (阿里云官方格式)")
    print("=" * 60)
    
    # 运行测试
    asyncio.run(main())