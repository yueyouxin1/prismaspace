from asyncio import Task
from dataclasses import dataclass
from typing import Any, Callable, Optional
from app.utils.async_generator import AsyncGeneratorManager 

@dataclass
class WorkflowRunResult:
    generator: AsyncGeneratorManager 
    trace_id: str
    run_id: str
    thread_id: str
    task: Optional[Task[Any]] = None
    cancel: Optional[Callable[[], None]] = None
    detach: Optional[Callable[[], None]] = None


@dataclass
class PreparedWorkflowRun:
    result: WorkflowRunResult
    background_task_kwargs: dict[str, Any]
