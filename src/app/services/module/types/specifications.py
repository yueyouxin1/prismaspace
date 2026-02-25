# src/app/services/module/types/specifications.py

from pydantic import BaseModel, Field
from typing import Optional, Literal, Union, List, Dict, Type, Any
from enum import Enum

# --- Enums ---

class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"

# --- Base Models ---
class BaseModuleAttributes(BaseModel):
    """所有模块固有规格的基类"""
    type: str # 判别器字段

class BaseModuleConfig(BaseModel):
    """所有模块可配置参数的基类"""
    type: str # 判别器字段

# --- LLM Specific Models ---
class LLMAttributes(BaseModuleAttributes):
    type: Literal["llm"] = "llm"
    client_name: str = Field(...)
    context_window: int = Field(..., description="The maximum context window size in tokens.")
    max_output_tokens: int = Field(..., description="The maximum output in tokens.")
    tool_calling_support: bool = Field(False, description="Whether the model supports tool calling.")
    json_mode_support: bool = Field(False, description="Whether the model supports JSON mode.")
    supported_modalities: Optional[List[Modality]] = Field(None, description="Supported modalities.")

class LLMConfig(BaseModuleConfig):
    type: Literal["llm"] = "llm"
    temperature: float = Field(0.7, ge=0, le=2, description="Controls randomness.")
    max_tokens: int = Field(2048, gt=0, description="The maximum number of tokens to generate.")
    top_p: float = Field(1.0, ge=0, le=1, description="Nucleus sampling parameter.")
    frequency_penalty: float = Field(0.0, ge=-2, le=2, description="Frequency penalty.")
    presence_penalty: float = Field(0.0, ge=-2, le=2, description="Presence penalty.")
    stop: Optional[List[str]] = Field(None, description="Stop sequences.")
    stream: bool = Field(False, description="Whether to stream the response.")
    response_format: Optional[Dict[str, Any]] = Field(None, description="Response format specification.")
    seed: Optional[int] = Field(None, description="Random seed.")

# --- Embedding Specific Models ---
class EmbeddingAttributes(BaseModuleAttributes):
    type: Literal["embedding"] = "embedding"
    client_name: str = Field(...)
    dimensions: int = Field(..., description="The dimensionality of the output vectors.")
    max_batch_size: int = Field(1, description="The maximum number of texts that can be processed in a single batch.")
    max_batch_tokens: int = Field(1024, description="The maximum total tokens allowed in a single batch request.")

class EmbeddingConfig(BaseModuleConfig):
    """Embedding models typically have no user-configurable runtime parameters."""
    type: Literal["embedding"] = "embedding"
    pass

class ToolAttributes(BaseModuleAttributes):
    type: Literal["tool"] = "tool"
    pass

class ToolConfig(BaseModuleConfig):
    type: Literal["tool"] = "tool"
    pass

# --- Discriminated Unions ---
# 这使得 Pydantic 可以根据 'type' 字段自动验证和解析正确的模型
AnyModuleAttributes = Union[LLMAttributes, EmbeddingAttributes, ToolAttributes]
AnyModuleConfig = Union[LLMConfig, EmbeddingConfig, ToolConfig]

# --- Registry for programmatic access ---
# 这是一个辅助工具，让我们可以通过字符串名称（来自数据库）动态获取模型类
_SPEC_REGISTRY: Dict[str, Dict[str, Type[BaseModel]]] = {
    "llm": {"attributes": LLMAttributes, "config": LLMConfig},
    "embedding": {"attributes": EmbeddingAttributes, "config": EmbeddingConfig},
    "tool": {"attributes": ToolAttributes, "config": ToolConfig},
    # 未来新的模块类型在这里注册
}

def get_spec_models(module_type_name: str) -> Dict[str, Type[BaseModel]]:
    """根据模块类型名称获取其对应的 Attributes 和 Config 模型类。"""
    models = _SPEC_REGISTRY.get(module_type_name)
    if not models:
        raise ValueError(f"No specification models registered for module type '{module_type_name}'")
    return models