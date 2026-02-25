# src/app/schemas/resource/uiapp/common.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Optional, Dict, Literal, Generic, TypeVar

T = TypeVar('T')

class ValueProperty(BaseModel, Generic[T]):
    """
    对应 contracts/ValueDefinition.ts
    """
    type: Literal['String', 'Number', 'Integer', 'Boolean', 'Object', 'List', 'Any'] = 'String'
    value: Optional[T] = None
    label: Optional[str] = None
    desc: Optional[str] = None
    required: bool = False
    
    # Added missing field from DSL
    behavior: Optional[Literal['data-binding-source', 'data-binding-target']] = None
    
    model_config = ConfigDict(extra='allow')

class StyleProperty(BaseModel):
    """
    Pass-through schema for contracts/Style.ts
    Validating the deeply nested style structure in Python is overkill.
    """
    states: Dict[str, Any] = Field(default_factory=dict, description="base, hover, focus, etc.")
    model_config = ConfigDict(extra='allow')