from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

class Streamable(ABC):
    """流式接口定义"""
    
    @abstractmethod
    async def get_result(self) -> Any:
        pass
        
    @abstractmethod
    async def cancel(self):
        pass

    @abstractmethod
    def subscribe(self) -> AsyncGenerator[Any, None]:
        pass