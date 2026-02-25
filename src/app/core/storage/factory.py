from typing import Dict, Type
from app.core.config import settings
from .base import BaseStorageProvider, ALL_STORAGE_PROVIDERS

# 导入具体实现以触发注册 (未来新增 S3 只需在这里 import)
from .aliyun_oss import AliyunOSSProvider
# from .aws_s3 import AWSS3Provider 

# 缓存已初始化的实例：Key=ProviderName, Value=ProviderInstance
_storage_instances: Dict[str, BaseStorageProvider] = {}

def get_storage_provider(name: str = None) -> BaseStorageProvider:
    """
    获取存储提供商实例。
    
    :param name: 指定 Provider 名称 (e.g., 'aliyun_oss', 'aws_s3').
                 如果不传，默认使用配置中的 STORAGE_PROVIDER。
    """
    global _storage_instances
    
    # 1. 确定要获取的 Provider 名称
    target_name = name or settings.STORAGE_PROVIDER
    
    # 2. 如果已缓存，直接返回
    if target_name in _storage_instances:
        return _storage_instances[target_name]
    
    # 3. 查找注册类
    provider_cls = ALL_STORAGE_PROVIDERS.get(target_name)
    if not provider_cls:
        available = list(ALL_STORAGE_PROVIDERS.keys())
        raise ValueError(
            f"Storage provider '{target_name}' not registered. "
            f"Available: {available}. "
            f"Ensure the implementation module is imported."
        )
    
    # 4. 初始化并缓存
    # 注意：这里假设所有 Provider 初始化都读取全局 settings。
    # 如果不同 Provider 需要不同配置（如 OSS 用 Key A，S3 用 Key B），
    # 则需要在 __init__ 中处理，或在此处传递特定 config。
    instance = provider_cls()
    _storage_instances[target_name] = instance
    
    return instance