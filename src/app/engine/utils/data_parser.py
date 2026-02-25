# engine/utils/data_parser.py

from pydantic import BaseModel
from typing import Optional, List, Dict, Any, AsyncGenerator
from hashlib import md5
import json, time, re
from datetime import datetime
import asyncio

# ====================================================================
# ===== 便捷函数 =====
# ====================================================================

async def merge_dicts_vanilla(dict_list: list):
    merged = {}
    if not dict_list:
        return merged
    for d in dict_list:
        for key, value in d.items():
            if key not in merged:
                merged[key] = []
            merged[key].append(value)
    return merged

async def array_to_object(arr, key_field):
    return {obj[key_field]: obj for obj in arr}

async def find_key_by_order(data, order):
    # 遍历字典的键值对
    for key, value in data.items():
        if value.get("order") == order:  # 检查 order 是否匹配
            return key  # 返回匹配的键名
    return None  # 如果没有找到，返回 None

async def split_expression(expression: str) -> list:
    """将表达式文本按照 {{ 和 }} 分割成数组"""
    parts = re.split(r'(\{\{.*?\}\})', expression)
    parts = [part for part in parts if part]
    return parts

async def get_value_by_path(data, path):
    if isinstance(path, list):
        path = '.'.join(path)

    if not isinstance(path, str):
        raise TypeError("path must be a string")

    # 使用正则表达式解析路径，保留数组索引
    keys = re.findall(r'\w+|\[\d+\]', path)

    if not keys:
        raise TypeError("path match is empty")
    
    value = data

    for i, key in enumerate(keys):
        if value is None:
            return None  # 路径不存在，返回 None

        if key.startswith('[') and key.endswith(']'):
            # 处理数组索引
            index = int(key[1:-1])
            if isinstance(value, list) and 0 <= index < len(value):
                value = value[index]
            else:
                return None  # 数组索引超出范围或类型不匹配
        else:
            # 处理字典键
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, list):
                # 处理嵌套数组，没有提供具体索引
                if i < len(keys) - 1:
                    next_key = keys[i + 1]
                    if next_key.startswith('[') and next_key.endswith(']'):
                        # 如果下一个键是数组索引，则继续遍历
                        value = [await get_value_by_path(item, '.'.join(keys[i:])) for item in value]
                        break
                    else:
                        # 否则，遍历数组并获取每个元素的值
                        value = [item.get(key) if isinstance(item, dict) else await get_value_by_path(item, '.'.join(keys[i:])) for item in value]
                else:
                    # 如果已经是路径的最后一部分，遍历数组并获取每个元素的值
                    value = [item.get(key) if isinstance(item, dict) else await get_value_by_path(item, '.'.join(keys[i:])) for item in value]
            else:
                return None  # 键不存在或类型不匹配

    return value  # 返回最终值

async def get_value_by_expr_template(template, data):
    """
    异步地替换模板字符串中的 {{ }} 表达式。
    
    :param template: 模板字符串
    :param data: 包含数据的字典
    :return: 替换后的字符串
    """
    if not isinstance(template, str):
        return template
    pattern = r'\{\{([^}]+)\}\}'
    matches = re.findall(pattern, template)
    
    # 如果没有占位符，直接返回原模板
    if not matches:
        return template
    
    # 如果只有一个占位符且整个模板就是占位符本身
    if len(matches) == 1 and template == f'{{{{{matches[0]}}}}}':
        value = await get_value_by_path(data, matches[0])
        return str(value) if value is not None else template

    async def process_match(match):
        value = await get_value_by_path(data, match)
        if value is None:
            return f'{{{{{match}}}}}'  # 如果找不到路径，保留原字符串
        return str(value)
    
    # 创建任务列表
    tasks = [process_match(match) for match in matches]
    
    # 并发执行任务
    results = await asyncio.gather(*tasks)
    
    # 构建替换后的字符串
    def replacement(match):
        index = matches.index(match.group(1))
        return results[index]
    
    return re.sub(pattern, replacement, template)

def get_default_value_by_type(type_str: str):
    """
    根据类型字符串返回对应类型的默认值。

    :param type_str: 类型字符串，如 'string', 'number', 'integer', 'boolean', 'date', 'object', 'array', 'file'
    :return: 对应类型的默认值
    """
    type_str = type_str.lower()
    if type_str == 'string':
        return ""
    elif type_str == 'number':
        return 0
    elif type_str == 'integer':
        return 0
    elif type_str == 'boolean':
        return False
    elif type_str == 'date':
        return datetime.now().date()  # 返回当前日期作为默认值
    elif type_str == 'object':
        return {}
    elif type_str == 'array':
        return []
    elif type_str == 'file':
        return None  # 文件类型通常没有默认值，返回 None
    else:
        return None  # 未知类型返回 None

def smart_cast_to_number(value: Any) -> Any:
    """
    Intelligently casts a value to a number (int or float).
    If it can be an integer without data loss, it returns an int.
    Otherwise, it returns a float.
    If it cannot be converted to a number, it returns a default value (0).
    """
    try:
        float_val = float(value)
        if float_val == int(float_val):
            return int(float_val)
        else:
            return float_val
    except (ValueError, TypeError):
        return 0 # Using 0 as the default for number.

def convert_value_by_type(value: Any, type_str: str) -> Any:
    """
    Safely converts a value to a specified type.
    If conversion fails, it returns a safe default value for that type.
    """
    if value is None:
        # None is a valid value for any type if not required, but here we are casting.
        # Returning the default is safer if None is unexpected.
        return get_default_value_by_type(type_str)

    # 1. Check if the type is already correct
    py_type_map = {'string': str, 'integer': int, 'number': float, 'boolean': bool, 'object': dict, 'array': list}
    if type_str in py_type_map and isinstance(value, py_type_map[type_str]):
        return value

    # 2. If not, attempt safe conversion        
    try:
        type_str_lower = type_str.lower()
        if type_str_lower == 'string':
            return str(value)
        elif type_str_lower == 'integer':
            return int(float(value))
        elif type_str_lower == 'number':
            return smart_cast_to_number(value)
        elif type_str_lower == 'boolean':
            if isinstance(value, str):
                return value.lower() == 'true'
            return bool(value)
        elif type_str_lower in ['object', 'array']:
            if isinstance(value, str):
                return json.loads(value)
            # If it's some other non-string, non-dict/list type, conversion is ambiguous.
            # Fallback to default.
            return get_default_value_by_type(type_str)
        else:
            # For unknown types, return the value as is.
            return value
    except Exception as e:
        return get_default_value_by_type(type_str)