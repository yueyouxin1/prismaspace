# engine/utils/parameter_schema_utils.py

import json
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from ..schemas.parameter_schema import ParameterSchema, SchemaBlueprint, ParameterValue
from .stream import Streamable
from .data_parser import get_value_by_path, get_value_by_expr_template, get_default_value_by_type, convert_value_by_type

# ========================================================================
# build_json_schema_node (from previous step, unchanged and correct)
# ========================================================================

def build_json_schema_node(param_schema: Union[ParameterSchema, SchemaBlueprint]) -> Dict[str, Any]:
    node_type = param_schema.type
    if not node_type:
        raise ValueError("Parameter properties must have a 'type'.")

    result = {"description": param_schema.description or ''}
    
    # [FIX] Add default value if it exists
    if 'default' in param_schema.model_fields_set:
        result['default'] = param_schema.default

    type_map = {'string': 'string', 'number': 'number', 'integer': 'integer', 'boolean': 'boolean'}

    if node_type in type_map:
        result['type'] = type_map[node_type]
        if param_schema.enum: result['enum'] = param_schema.enum
    
    elif node_type == 'object':
        result['type'] = 'object'
        properties = {}
        required = []
        for sub_param in param_schema.properties or []:
            prop_name = sub_param.name
            if not prop_name: continue
            properties[prop_name] = build_json_schema_node(sub_param)
            if sub_param.required: required.append(prop_name)
        result['properties'] = properties
        if required: result['required'] = required

    elif node_type == 'array':
        result['type'] = 'array'
        items_schema = param_schema.items
        if not items_schema: raise ValueError("Array type must have an 'items' properties.")
        result['items'] = build_json_schema_node(items_schema)
    
    else:
        raise ValueError(f"Unsupported parameter type: {node_type}")

    return result

# ========================================================================
# schema_filler (The new, powerful, universal implementation)
# ========================================================================

async def schemas2obj(target_schema: List[ParameterSchema], context: Optional[Dict[str, Any]] = None, real_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    The main entry point. Creates a structured object based on the target_schema.
    It intelligently fills the structure using a combination of:
    1. `real_data` (e.g., an API response) - Highest priority.
    2. `value` definitions within the properties (for refs, literals, exprs) - Medium priority.
    3. `default` values within the properties - Lowest priority.
    4. Type-based defaults (e.g., "" for string) - Fallback.
    
    This function is adapted from the robust `schemas2obj` logic.
    """
    if real_data is None: real_data = {}
    if context is None: context = {}
    
    result = {}
    if not isinstance(target_schema, list): return result

    for item_schema in target_schema:
        if not isinstance(item_schema, ParameterSchema): continue
        item_name = item_schema.name
        if not item_name: continue

        # Pass the corresponding part of the real_data for processing.
        current_real_data = real_data.get(item_name)
        result[item_name] = await _process_schema_node(item_schema, context, current_real_data)
        
    return result

async def _process_schema_node(item_schema: Union[ParameterSchema, SchemaBlueprint], context: Dict[str, Any], real_data_for_item: Any):
    """
    Recursively processes a single properties node to determine its final value.
    This is the core recursive worker.
    """
    item_type = item_schema.type
    # --- Step 1: Determine the raw value based on priority ---
    return_value = None
    priority_source = "none" # For debugging

    # Priority 1: Use real_data if it exists.
    if real_data_for_item is not None:
        return_value = real_data_for_item
        priority_source = "real_data"
    
    # Priority 2: Evaluate 'value' definition (ref, literal, etc.)
    elif isinstance(item_schema, ParameterSchema) and item_schema.value:
        value_type = item_schema.value.type
        content = item_schema.value.content
        priority_source = value_type

        if value_type == 'literal':
            return_value = content
        elif value_type == 'ref':
            # This part is for workflow context, but we implement it for future-proofing.
            # In a stateless plugin call, `context` will be empty.
            block_id = getattr(content, 'blockID', None)
            path = getattr(content, 'path', None)
            if block_id and path and context:
                source_data = context.get(block_id)
                if isinstance(source_data, Streamable):
                    source_data = await source_data.get_result()
                if source_data and path:
                    return_value = await get_value_by_path(source_data, path)
        # elif value_type == 'expr':
            # Expression evaluation logic would go here
    
    # Priority 3: Use the properties's 'default' value.
    if return_value is None and 'default' in item_schema.model_fields_set:
        return_value = item_schema.default
        priority_source = "default"

    # --- Step 2: Shape the return_value according to the properties's type ---
    if item_type == 'object':
        # Ensure return_value is a dict for processing; otherwise, start with an empty dict.
        source_obj = convert_value_by_type(return_value, item_type)
        sub_schemas = item_schema.properties or []
        
        # Always return a dictionary. If no sub-schemas, return the source object (or empty).
        if not sub_schemas:
            return source_obj
        
        # Recursively call the main function to process sub-schemas.
        # This ensures consistent filling and shaping logic at all levels.
        return await schemas2obj(sub_schemas, context, source_obj)

    elif item_type == 'array':
        # Ensure return_value is a list; otherwise, start with an empty list.
        source_list = convert_value_by_type(return_value, item_type)
        items_blueprint = item_schema.items
        
        # If no item properties is defined, we cannot shape the items. Return the list as is.
        if not items_blueprint or not isinstance(items_blueprint, SchemaBlueprint):
            return [] # source_list

        # **关键修正**: 如果 source_list 为空，但 items_blueprint 存在，
        # 这意味着我们需要“从零构建”一个包含示例项的列表。
        if not source_list:
            # 递归调用自身来构建一个默认的列表项
            default_item = await _process_schema_node(items_blueprint, context, None)
            return [default_item]
            
        # 遍历继承的列表，对每一项应用 sub_schema 进行塑形
        shaped_list = []
        for item_data in source_list:
            # 将列表中的项作为 real_data 传递给下一层递归进行处理
            shaped_item = await _process_schema_node(items_blueprint, context, item_data)
            shaped_list.append(shaped_item)
        return shaped_list

    else: # --- Step 3: Handle primitive types and final fallbacks ---
        # If after all priority checks, return_value is still None, apply final fallback.
        if return_value is None:
            return get_default_value_by_type(item_type)
        
    # Here you could add type casting for robustness (e.g., int(return_value))
    return convert_value_by_type(return_value, item_type)