# app/constants/service_module_constants.py

from .permission_constants import SERVICE_MODULE_ROOT_PERM
from app.models import ServiceModuleStatus

SERVICE_MODULE_TYPES_DATA = [
    {'name': 'llm', 'label': '语言模型'},
    {'name': 'vlm', 'label': '视觉模型'},
    {'name': 'embedding', 'label': '文本嵌入模型'},
    {'name': 'tts', 'label': '语音合成'},
]

SERVICE_MODULES_DATA = [
    {
        'type_name': 'llm',
        'name': 'qwen-plus',
        'label': 'qwen-plus',
        'provider': 'aliyun',
        'versions': [
            {
                'name': 'qwen-plus-2025-09-11',
                'version_tag': '2025-09-11',
                'status': ServiceModuleStatus.AVAILABLE,
                'attributes': {
                    'max_context_tokens': 1000000
                }
            }
        ],
        'permission': {'name': f'{SERVICE_MODULE_ROOT_PERM}:use:aliyun:qwen-plus', 'label': "使用 aliyun qwen-plus", 'type': ActionPermissionType.API, 'is_assignable': False},
    },
    {
        'type_name': 'llm',
        'name': 'gpt-4o',
        'label': 'gpt-4o',
        'provider': 'openai',
        'versions': [
            {
                'version_tag': '2024-05-13',
                'status': ServiceModuleStatus.AVAILABLE,
                'attributes': {
                    'max_tokens': 8192
                }
            }
        ],
        'permission': {'name': f'{SERVICE_MODULE_ROOT_PERM}:use:openai:gpt-4o', 'label': "使用 openai qwen3-32b", 'type': ActionPermissionType.API, 'is_assignable': False},
    },
    {
        'type_name': 'embedding',
        'name': 'text-embedding-v4',
        'label': 'text-embedding-v4',
        'provider': 'aliyun',
        'versions': [
            {
                'version_tag': '1.0.0',
                'status': ServiceModuleStatus.AVAILABLE,
                'attributes': {
                    'dimensions': 1536, 
                    'max_tokens': 8192
                }
            }
        ],
        'permission': {'name': f'{SERVICE_MODULE_ROOT_PERM}:use:aliyun:text-embedding-v4', 'label': "使用 aliyun text-embedding-v4", 'type': ActionPermissionType.API, 'is_assignable': False},
    }
]