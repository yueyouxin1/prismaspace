# src/app/core/storage/base.py

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, NamedTuple, Type, TypeVar
from enum import Enum

class StorageType(str, Enum):
    ALIYUN_OSS = "aliyun_oss"
    AWS_S3 = "aws_s3"
    MINIO = "minio"

class ObjectMetadata(NamedTuple):
    hash_str: str # ETag or MD5
    size: int
    content_type: str

class UploadTicket(NamedTuple):
    """
    标准化的上传凭证对象。
    前端使用此信息直接向云存储发起上传请求。
    """
    upload_url: str          # 上传的目标地址 (Host)
    form_data: Dict[str, Any] # 需要放入 multipart/form-data 的字段 (Signature, Policy, Key, etc.)
    provider: str            # 提供商标识，方便前端适配不同上传逻辑
    physical_key: str        # 文件在存储桶中的实际 Key

class BaseStorageProvider(ABC):
    """
    存储提供商抽象基类。
    所有具体实现（OSS, S3）必须继承此类并定义 `name` 属性。
    """
    name: str = "base"

    @abstractmethod
    def generate_upload_ticket(
        self, 
        key: str, 
        mime_type: Optional[str] = None,
        max_size_bytes: int = 104857600, # 100MB Default
        expire_seconds: int = 60
    ) -> UploadTicket:
        """
        生成客户端直传的签名/凭证。
        
        :param key: 文件在存储桶中的路径 (e.g., "assets/2023/10/uuid.jpg")
        :param mime_type: 限制上传文件的类型
        :param max_size_bytes: 限制上传文件的大小
        :param expire_seconds: 签名的有效期
        """
        raise NotImplementedError

    @abstractmethod
    async def get_object_metadata(self, key: str) -> ObjectMetadata:
        """
        获取云端对象的元数据 (Head Object)。
        用于校验上传是否成功，并获取权威 Hash。
        """
        raise NotImplementedError
    
    @abstractmethod
    async def download_object(self, key: str) -> bytes:
        """下载对象内容"""
        raise NotImplementedError

    @abstractmethod
    async def delete_object(self, key: str) -> bool:
        """
        删除存储桶中的对象。
        """
        raise NotImplementedError

    @abstractmethod
    async def object_exists(self, key: str) -> bool:
        """
        检查对象是否存在（用于 confirm_upload 阶段的校验）。
        """
        raise NotImplementedError

    @abstractmethod
    def get_public_url(self, key: str) -> str:
        """
        获取文件的访问 URL。
        如果配置了 CDN，应返回 CDN 地址。
        """
        raise NotImplementedError

# 定义注册表
ALL_STORAGE_PROVIDERS: Dict[str, Type[BaseStorageProvider]] = {}

T = TypeVar('T', bound=BaseStorageProvider)

def register_storage_provider(cls: Type[T]) -> Type[T]:
    """
    装饰器：注册存储提供商实现类。
    """
    if not hasattr(cls, 'name') or not cls.name:
        raise ValueError(f"Storage provider class {cls.__name__} must define a 'name' attribute.")
    
    if cls.name in ALL_STORAGE_PROVIDERS:
        raise ValueError(f"Storage provider with name '{cls.name}' already registered.")
    
    ALL_STORAGE_PROVIDERS[cls.name] = cls
    return cls