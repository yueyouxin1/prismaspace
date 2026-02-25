# src/app/models/resource/agent/__init__.py
from .agent import Agent
from .agent_memory import MemoryScope, MemoryType, AgentMemoryVar, AgentMemoryVarValue, SummaryScope, AgentContextSummary
from ..base import ALL_INSTANCE_TYPES

ALL_INSTANCE_TYPES.append(Agent)