from asyncio import Task
from dataclasses import dataclass
from typing import Any, Optional
from app.utils.async_generator import AsyncGeneratorManager 

@dataclass
class WorkflowRunResult:
    generator: AsyncGeneratorManager 
    trace_id: str
    task: Optional[Task[Any]] = None
