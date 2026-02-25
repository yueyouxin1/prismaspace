# engine/tool/callbacks.py

from typing import Protocol, Dict, Any

class ToolEngineCallbacks(Protocol):
    """
    定义了 ToolEngineService 在执行过程中向外报告事件的接口。
    [修正] 所有参数均为原生 Python 类型。
    """
    async def on_start(self, context: Dict[str, Any], inputs: Dict[str, Any]) -> None:
        """在执行开始时调用。context 用于传递日志所需的元数据。"""
        ...

    async def on_log(self, message: str, metadata: Dict[str, Any] = None) -> None:
        ...

    async def on_success(self, result: Dict[str, Any], raw_response: Any) -> None:
        ...
        
    async def on_error(self, error: Exception) -> None:
        ...