from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class WorkflowUiMountPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    interaction_id: str = Field(alias="interactionId")
    uiapp_instance_uuid: Optional[str] = Field(default=None, alias="uiappInstanceUuid")
    page_key: Optional[str] = Field(default=None, alias="pageKey")
    dsl: Optional[Dict[str, Any]] = None
    props: Dict[str, Any] = Field(default_factory=dict)
    state: Dict[str, Any] = Field(default_factory=dict)


class WorkflowUiPatchPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    interaction_id: str = Field(alias="interactionId")
    patch: Dict[str, Any] = Field(default_factory=dict)


class WorkflowUiUnmountPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    interaction_id: str = Field(alias="interactionId")
    reason: Optional[str] = None
