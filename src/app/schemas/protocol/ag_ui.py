from typing import Dict, List, Literal, Optional, TypeAlias

from pydantic import Field, JsonValue

from ag_ui.core import RunAgentInput, RunFinishedEvent
from ag_ui.core.events import BaseEvent
from ag_ui.core.types import ConfiguredBaseModel, Context, Message, Tool, ToolCall


class AgUiResumeToolResult(ConfiguredBaseModel):
    tool_call_id: str = Field(alias="toolCallId")
    content: JsonValue = None


class AgUiResumePayload(ConfiguredBaseModel):
    tool_results: List[AgUiResumeToolResult] = Field(default_factory=list, alias="toolResults")


class AgUiResume(ConfiguredBaseModel):
    interrupt_id: str = Field(alias="interruptId")
    payload: AgUiResumePayload = Field(default_factory=AgUiResumePayload)


class AgUiInterruptToolCall(ConfiguredBaseModel):
    tool_call_id: str = Field(alias="toolCallId")
    name: str
    arguments: JsonValue = Field(default_factory=dict)


class AgUiInterruptPayload(ConfiguredBaseModel):
    tool_calls: List[AgUiInterruptToolCall] = Field(default_factory=list, alias="toolCalls")


class AgUiInterrupt(ConfiguredBaseModel):
    id: Optional[str] = None
    reason: Optional[str] = None
    payload: Optional[AgUiInterruptPayload] = None


class RunAgentInputExt(RunAgentInput):
    resume: Optional[AgUiResume] = None


class RunFinishedEventExt(RunFinishedEvent):
    outcome: Optional[Literal["success", "interrupt", "cancelled"]] = None
    interrupt: Optional[AgUiInterrupt] = None


class RunEventsResponse(ConfiguredBaseModel):
    thread_id: str = Field(alias="threadId")
    run_id: str = Field(alias="runId")
    events: List[Dict[str, JsonValue]]


AgUiMessage: TypeAlias = Message
AgUiTool: TypeAlias = Tool
AgUiContext: TypeAlias = Context
AgUiToolCall: TypeAlias = ToolCall
AgUiBaseEvent: TypeAlias = BaseEvent
