import asyncio
import logging
import json
import base64
import hmac
import time
import datetime
from hashlib import sha1 as sha
from typing import Optional, Dict, Any
from functools import partial

import oss2  # type: ignore
from app.core.config import settings
from .base import register_storage_provider, BaseStorageProvider, StorageType, ObjectMetadata, UploadTicket

logger = logging.getLogger(__name__)

@register_storage_provider
class AliyunOSSProvider(BaseStorageProvider):
    name: str = "aliyun_oss"

    def __init__(self):
        # 阿里云 OSS2 库是同步的，需要专门的 Auth 实例
        self.auth = oss2.Auth(settings.STORAGE_ACCESS_KEY, settings.STORAGE_SECRET_KEY)
        self.bucket_name = settings.STORAGE_BUCKET
        self.endpoint = settings.STORAGE_ENDPOINT
        # 初始化 Bucket 对象 (轻量级，不涉及网络请求)
        self.bucket = oss2.Bucket(self.auth, self.endpoint, self.bucket_name)
        
        self.public_domain = settings.STORAGE_PUBLIC_DOMAIN or f"https://{self.bucket_name}.{self.endpoint}"

    def _generate_expiration(self, seconds: int) -> str:
        now = int(time.time())
        expiration_time = now + seconds
        return datetime.datetime.utcfromtimestamp(expiration_time).strftime('%Y-%m-%dT%H:%M:%SZ')

    async def _run_in_executor(self, func, *args, **kwargs):
        """
        将同步 IO 操作放入线程池执行，避免阻塞 Async Event Loop
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    def generate_upload_ticket(
        self, 
        key: str, 
        mime_type: Optional[str] = None,
        max_size_bytes: int = settings.STORAGE_MAX_UPLOAD_SIZE_BYTES,
        expire_seconds: int = settings.STORAGE_UPLOAD_EXPIRE_SECONDS
    ) -> UploadTicket:
        """
        生成签名是一个纯 CPU 操作（加密计算），不会阻塞 IO，因此不需要 async。
        """
        expiration_iso = self._generate_expiration(expire_seconds)
        
        conditions = [
            ["content-length-range", 0, max_size_bytes],
            ["eq", "$key", key],
            ["eq", "$success_action_status", "200"]
        ]
        
        if mime_type:
            conditions.append(["eq", "$content-type", mime_type])

        policy_dict = {
            "expiration": expiration_iso,
            "conditions": conditions
        }
        
        policy_json = json.dumps(policy_dict).strip()
        policy_encode = base64.b64encode(policy_json.encode())
        
        h = hmac.new(settings.STORAGE_SECRET_KEY.encode(), policy_encode, sha)
        signature = base64.b64encode(h.digest()).decode()

        form_data = {
            'OSSAccessKeyId': settings.STORAGE_ACCESS_KEY,
            'policy': policy_encode.decode(),
            'Signature': signature,
            'key': key,
            'success_action_status': '200',
        }
        
        if mime_type:
            form_data['content-type'] = mime_type

        return UploadTicket(
            upload_url=self.public_domain, 
            form_data=form_data,
            provider=StorageType.ALIYUN_OSS.value,
            physical_key=key
        )

    async def get_object_metadata(self, key: str) -> ObjectMetadata:
        try:
            result = await self._run_in_executor(self.bucket.head_object, key)
            # OSS ETag 包含双引号，需要去除
            etag = result.etag.strip('"')
            return ObjectMetadata(
                hash_str=etag,
                size=result.content_length,
                content_type=result.content_type
            )
        except oss2.exceptions.OssError as e:
            # 区分 404
            if e.status == 404:
                raise FileNotFoundError(f"OSS Object not found: {key}")
            raise e

    async def download_object(self, key: str) -> bytes:
        try:
            result = await self._run_in_executor(self.bucket.get_object, key)
            return result.read()
        except oss2.exceptions.OssError as e:
            raise e

    async def delete_object(self, key: str) -> bool:
        """
        异步封装的删除操作
        """
        try:
            # 使用线程池执行同步的 bucket.delete_object
            await self._run_in_executor(self.bucket.delete_object, key)
            return True
        except oss2.exceptions.OssError as e:
            logger.error(f"OSS Delete Error: {str(e)} Key: {key}")
            return False

    async def object_exists(self, key: str) -> bool:
        """
        异步封装的存在性检查
        """
        try:
            return await self._run_in_executor(self.bucket.object_exists, key)
        except oss2.exceptions.OssError as e:
            logger.error(f"OSS Exists Check Error: {str(e)} Key: {key}")
            return False

    def get_public_url(self, key: str) -> str:
        # 纯字符串拼接，无需 IO
        base = self.public_domain.rstrip('/')
        clean_key = key.lstrip('/')
        return f"{base}/{clean_key}"