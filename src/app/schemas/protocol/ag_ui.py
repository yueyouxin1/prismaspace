from typing import Dict, List, Literal, Optional, TypeAlias

from pydantic import ConfigDict, Field, JsonValue, field_validator

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


class RunAgentPlatformProps(ConfiguredBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=ConfiguredBaseModel.model_config.get("alias_generator"),
        populate_by_name=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    session_mode: Optional[Literal["auto", "stateless", "stateful"]] = Field(
        default=None,
        description="Platform session policy. Canonical values: auto, stateless, stateful.",
    )
    protocol: Optional[Literal["ag-ui"]] = Field(
        default=None,
        description="Platform protocol selector. Current supported value: ag-ui.",
    )
    agent_uuid: Optional[str] = Field(
        default=None,
        description="WebSocket-only agent instance UUID used for transport routing.",
    )

    @field_validator("session_mode", mode="before")
    @classmethod
    def normalize_session_mode(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("sessionMode must be a string.")

        normalized = value.strip().lower()
        if not normalized:
            return None

        aliases = {
            "auto": "auto",
            "stateless": "stateless",
            "session": "stateful",
            "stateful": "stateful",
        }
        resolved = aliases.get(normalized)
        if not resolved:
            raise ValueError("sessionMode must be one of: auto, stateless, stateful.")
        return resolved

    @field_validator("protocol", mode="before")
    @classmethod
    def normalize_protocol(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("protocol must be a string.")

        normalized = value.strip().lower()
        if not normalized:
            return None

        aliases = {
            "agui": "ag-ui",
            "ag-ui": "ag-ui",
        }
        resolved = aliases.get(normalized)
        if not resolved:
            raise ValueError("protocol must be 'ag-ui'.")
        return resolved

    @field_validator("agent_uuid", mode="before")
    @classmethod
    def normalize_agent_uuid(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("agentUuid must be a string.")

        text = value.strip()
        return text or None


class RunAgentForwardedProps(ConfiguredBaseModel):
    platform: Optional[RunAgentPlatformProps] = Field(
        default=None,
        description="Platform-reserved forwarded properties. Other top-level keys remain available for transport or middleware extensions.",
    )


class RunAgentInputExt(RunAgentInput):
    forwarded_props: RunAgentForwardedProps = Field(alias="forwardedProps")
    resume: Optional[AgUiResume] = None

    @property
    def platform_props(self) -> Optional[RunAgentPlatformProps]:
        return self.forwarded_props.platform


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
