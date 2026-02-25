# src/app/schemas/module/service_module_credential_schemas.py

from pydantic import BaseModel, Field, ConfigDict, HttpUrl
from typing import Optional, List, Dict, Any

# --- Input Schemas ---

class ServiceModuleCredentialCreate(BaseModel):
    provider_id: int = Field(..., description="Service provider id")
    label: str = Field(..., min_length=1, max_length=255, description="A user-friendly display name for the credential")
    value: str = Field(..., min_length=1, description="The plaintext API key or credential value")
    endpoint: Optional[HttpUrl] = Field(None, description="[Optional] A custom API endpoint URL.")
    region: Optional[str] = Field(None, description="[Optional] The service region.")
    attributes: Optional[Dict[str, Any]] = Field(None, description="[Optional] Other provider-specific attributes.")

class ServiceModuleCredentialUpdate(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=255, description="A new user-friendly label")
    value: Optional[str] = Field(None, min_length=1, description="A new plaintext value to replace the old one")
    endpoint: Optional[HttpUrl] = Field(None, description="[Optional] A new custom API endpoint URL.")
    region: Optional[str] = Field(None, description="[Optional] A new service region.")
    attributes: Optional[Dict[str, Any]] = Field(None, description="[Optional] A new set of other attributes.")

# --- Output Schemas ---

class ServiceModuleCredentialRead(BaseModel):
    uuid: str
    provider_id: int
    label: str
    # 只暴露非敏感信息
    region: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    
    model_config = ConfigDict(from_attributes=True)

class ProviderInfo(BaseModel):
    name: str
    label: str