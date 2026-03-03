from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


AgUiRole = Literal["developer", "system", "assistant", "user", "tool", "activity", "reasoning"]


class AgUiToolCallFunction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    arguments: str


class AgUiToolCall(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: Literal["function"] = "function"
    function: AgUiToolCallFunction
    encrypted_value: Optional[str] = Field(default=None, alias="encryptedValue")


class AgUiMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    role: AgUiRole
    content: Optional[Any] = None
    name: Optional[str] = None
    tool_calls: Optional[List[AgUiToolCall]] = Field(default=None, alias="toolCalls")
    tool_call_id: Optional[str] = Field(default=None, alias="toolCallId")
    error: Optional[str] = None
    encrypted_value: Optional[str] = Field(default=None, alias="encryptedValue")
    activity_type: Optional[str] = Field(default=None, alias="activityType")


class AgUiTool(BaseModel):
    name: str
    description: str
    parameters: Any


class AgUiContext(BaseModel):
    description: str
    value: str


class AgUiResume(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    interrupt_id: Optional[str] = Field(default=None, alias="interruptId")
    payload: Any = None


class AgUiRunAgentInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    thread_id: str = Field(alias="threadId")
    run_id: str = Field(alias="runId")
    parent_run_id: Optional[str] = Field(default=None, alias="parentRunId")
    state: Any = Field(default_factory=dict)
    messages: List[AgUiMessage] = Field(default_factory=list)
    tools: List[AgUiTool] = Field(default_factory=list)
    context: List[AgUiContext] = Field(default_factory=list)
    forwarded_props: Any = Field(default_factory=dict, alias="forwardedProps")
    resume: Optional[AgUiResume] = None

