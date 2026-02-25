# src/app/schemas/module/service_module_provider_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional

class ServiceModuleProviderBase(BaseModel):
    name: str = Field(..., pattern=r'^[a-z_]+$', description="The unique identifier for the provider (e.g., 'openai', 'aliyun')")
    label: str = Field(..., description="The human-readable name for UIs (e.g., '阿里云')")
    description: Optional[str] = None

class ServiceModuleProviderCreate(ServiceModuleProviderBase):
    pass

class ServiceModuleProviderUpdate(BaseModel):
    label: Optional[str] = Field(None, description="A new human-readable name")
    description: Optional[str] = None

class ServiceModuleProviderRead(ServiceModuleProviderBase):
    id: int
    model_config = ConfigDict(from_attributes=True)