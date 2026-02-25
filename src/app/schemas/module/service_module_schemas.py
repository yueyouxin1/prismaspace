# src/app/schemas/module/service_module_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from app.models import ServiceModuleStatus
from app.services.module.types.specifications import AnyModuleAttributes, AnyModuleConfig

class ServiceModuleVersionCreate(BaseModel):
    name: str = Field(..., description="This version's unique name (e.g., 'gpt-4o-2024-05-13')")
    version_tag: str = Field(..., description="The version tag (e.g., '2024-05-13')")
    description: Optional[str] = None
    is_default: bool = Field(False, description="是否将此版本设为该 Module Type 的系统默认版本")
    status: ServiceModuleStatus = Field(ServiceModuleStatus.AVAILABLE)
    attributes: AnyModuleAttributes = Field(..., description="Immutable specifications object for the module version.")
    config: AnyModuleConfig = Field(..., description="Default configuration object for the module version.")

class ServiceModuleCreate(BaseModel):
    type_name: str = Field(..., description="The type of this module (e.g., 'llm', 'embedding')")
    name: str = Field(..., pattern=r'^[a-zA-Z0-9_-]+$', description="Module's unique name (alphanumeric, -, _)")
    label: str
    description: Optional[str] = None
    provider_name: str = Field(..., pattern=r'^[a-zA-Z0-9_-]+$', description="Service provider unique name (alphanumeric, -, _)")
    requires_credential: bool = Field(False)

class ServiceModuleCreateFull(BaseModel):
    """A composite schema for the creation API endpoint."""
    module: ServiceModuleCreate
    versions: List[ServiceModuleVersionCreate]

class ServiceModuleVersionRead(BaseModel):
    uuid: str
    version_tag: str
    description: Optional[str] = None #
    attributes: Dict[str, Any]
    config: Dict[str, Any]
    
    model_config = ConfigDict(from_attributes=True)

class ServiceModuleRead(BaseModel):
    name: str
    label: str
    provider_id: int
    versions: List[ServiceModuleVersionRead]
    model_config = ConfigDict(from_attributes=True)