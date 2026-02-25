# src/app/schemas/project/project_env_schemas.py

from typing import Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class ProjectEnvConfigUpdate(BaseModel):
    env_config: Dict[str, Any] = Field(default_factory=dict, description="项目级环境配置")


class ProjectEnvConfigRead(BaseModel):
    env_config: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)
