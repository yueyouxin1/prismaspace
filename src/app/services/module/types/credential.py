# src/app/services/module/types/credential.py

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, Dict, Any

class ResolvedCredential(BaseModel):
    """
    一个已解析的、随时可用的凭证对象。
    它包含了解密后的API Key、端点URL以及其他必要的运行时信息。
    """
    api_key: str = Field(..., description="The plaintext API key.")
    endpoint: Optional[HttpUrl] = Field(None, description="The API endpoint URL.")
    region: Optional[str] = Field(None, description="The service region, if applicable.")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Other provider-specific attributes.")
    is_custom: bool = Field(False, description="True if provided by user (BYOK), False if platform default.")