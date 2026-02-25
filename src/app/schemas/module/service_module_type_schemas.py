# src/app/schemas/module/service_module_type_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional

class ServiceModuleTypeBase(BaseModel):
    name: str = Field(..., pattern=r'^[a-z_]+$', description="The unique identifier for the type (e.g., 'llm', 'embedding')")
    label: str = Field(..., description="The human-readable name for UIs (e.g., 'Large Language Model')")
    description: Optional[str] = None

class ServiceModuleTypeCreate(ServiceModuleTypeBase):
    pass

class ServiceModuleTypeUpdate(BaseModel):
    label: Optional[str] = Field(None, description="A new human-readable name")
    description: Optional[str] = None

class ServiceModuleTypeRead(ServiceModuleTypeBase):
    id: int
    model_config = ConfigDict(from_attributes=True)