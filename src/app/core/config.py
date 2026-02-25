# app/core/config.py
import os
from dotenv import load_dotenv
load_dotenv(".env")
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, HttpUrl, field_validator, computed_field, model_validator
from typing import Optional, List, Literal, Dict, Any
from app.models import Currency

class Settings(BaseSettings):
    # model_config 会自动加载 .env 文件
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # 应用配置 (会自动转换类型)
    APP_ENV: str = "production"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000

    SITE_CURRENCY: Currency = Field(..., description="The authoritative currency for this site deployment.")

    # --- [修改] Database ---
    DB_HOST: str
    DB_PORT: int = 5432 # 默认端口改为 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        # 核心修改：连接字符串格式改为 postgresql+asyncpg
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    DB_TENANT_DATA_HOST: str
    DB_TENANT_DATA_PORT: int = 5432
    DB_TENANT_DATA_USER: str
    DB_TENANT_DATA_PASSWORD: str
    DB_TENANT_DATA_NAME: str

    @computed_field
    @property
    def DATABASE_URL_TENANT_DATA(self) -> str:
        return f"postgresql+asyncpg://{self.DB_TENANT_DATA_USER}:{self.DB_TENANT_DATA_PASSWORD}@{self.DB_TENANT_DATA_HOST}:{self.DB_TENANT_DATA_PORT}/{self.DB_TENANT_DATA_NAME}"

    # --- [修改] TestDatabase ---
    DB_TEST_HOST: str
    DB_TEST_PORT: int = 5432
    DB_TEST_USER: str
    DB_TEST_PASSWORD: str
    DB_TEST_NAME: str
    
    @computed_field
    @property
    def DATABASE_URL_TEST(self) -> str:
        return f"postgresql+asyncpg://{self.DB_TEST_USER}:{self.DB_TEST_PASSWORD}@{self.DB_TEST_HOST}:{self.DB_TEST_PORT}/{self.DB_TEST_NAME}"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_DB: int = 0

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        password = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{password}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # Test Redis
    REDIS_TEST_HOST: str = "localhost"
    REDIS_TEST_PORT: int = 6379
    REDIS_TEST_PASSWORD: Optional[str] = None
    REDIS_TEST_DB: int = 1

    @computed_field
    @property
    def REDIS_URL_TEST(self) -> str:
        password = f":{self.REDIS_TEST_PASSWORD}@" if self.REDIS_TEST_PASSWORD else ""
        return f"redis://{password}{self.REDIS_TEST_HOST}:{self.REDIS_TEST_PORT}/{self.REDIS_TEST_DB}"
        
    # VECTORDB
    VECTOR_ENGINES_ENABLED: List[str] = ["default"]

    @computed_field
    @property
    def VECTOR_ENGINE_CONFIGS(self) -> List[Dict[str, Any]]:
        configs = []

        for alias in self.VECTOR_ENGINES_ENABLED:
            prefix = f"VE_{alias.upper()}_"

            engine_type = os.getenv(prefix + "TYPE")
            host = os.getenv(prefix + "HOST")
            port = os.getenv(prefix + "PORT")

            if engine_type is None or host is None or port is None:
                raise ValueError(
                    f"Missing required VECTOR ENGINE settings for alias '{alias}'. "
                    f"Expected variables: {prefix}TYPE, {prefix}HOST, {prefix}PORT"
                )

            configs.append({
                "engine_type": engine_type,
                "host": host,
                "port": int(port),
                "alias": alias,
            })

        return configs

    TIKA_SERVER_URL: HttpUrl = Field(
        "http://localhost:9998/tika", 
        description="URL for the Apache Tika server used for document parsing."
    )

    # --- Storage Infrastructure ---
    # 定义使用的存储提供商: 'aliyun_oss', 'aws_s3', 'minio'
    STORAGE_PROVIDER: Literal["aliyun_oss", "aws_s3", "minio"] = "aliyun_oss"
    
    # Storage Access Credentials
    STORAGE_ENDPOINT: str = Field(..., description="e.g., oss-cn-hangzhou.aliyuncs.com")
    STORAGE_BUCKET: str = Field(..., description="Bucket name")
    STORAGE_ACCESS_KEY: str = Field(..., description="Access Key ID")
    STORAGE_SECRET_KEY: str = Field(..., description="Access Key Secret")
    
    # [Optional] CDN / Public Domain
    # 如果配置了CDN，生成的URL将使用此域名而不是 Endpoint
    STORAGE_PUBLIC_DOMAIN: Optional[str] = None
    
    # 默认上传过期时间 (秒)
    STORAGE_UPLOAD_EXPIRE_SECONDS: int = 60
    # 限制上传最大 500MB
    STORAGE_MAX_UPLOAD_SIZE_BYTES: int = 524288000 
    # 允许的默认 Bucket ACL
    STORAGE_BUCKET_ACL: str = "private"
    
    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    CREDENTIAL_ENCRYPTION_KEY: Optional[str] = None
    OPENAI_API_URL: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ALIYUN_API_URL: Optional[str] = None
    ALIYUN_API_KEY: Optional[str] = None

settings = Settings()