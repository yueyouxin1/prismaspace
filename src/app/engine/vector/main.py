# engine/vector/main.py
import asyncio
from typing import Dict, List
from .base import VectorEngineService, VectorEngineConfig, VectorEngineError
from .milvus_engine import MilvusEngine
# from .qdrant_engine import QdrantEngine # For future extension

class VectorEngineManager:
    """
    [核心管理器]
    管理所有向量引擎的配置和生命周期。
    - 在应用启动时初始化。
    - 懒加载并缓存底层客户端连接。
    - 按需提供具体的、请求作用域的 VectorEngineService 实例。
    """
    def __init__(self, configs: List[VectorEngineConfig]):
        self._configs: Dict[str, VectorEngineConfig] = {c.alias: c for c in configs}
        self._clients: Dict[str, any] = {} # 缓存已连接的底层客户端 (e.g., MilvusClient)
        self._client_locks: Dict[str, asyncio.Lock] = {c.alias: asyncio.Lock() for c in configs}

    async def startup(self):
        """
        [生命周期] 可以在启动时预连接默认客户端，或保持完全懒加载。
        为简单起见，我们保持懒加载，此方法目前为空。
        """
        print(f"VectorEngineManager initialized with {len(self._configs)} configurations.")

    async def shutdown(self):
        """[生命周期] 安全关闭所有已建立的客户端连接。"""
        print("Shutting down VectorEngineManager, closing all clients...")
        for alias, client in self._clients.items():
            config = self._configs.get(alias)
            if not config:
                raise VectorEngineError(f"Configuration with alias '{alias}' not found.")
            try:
                if config.engine_type == 'milvus':
                    client.close()
                print(f"Client for alias '{alias}' closed.")
            except Exception as e:
                print(f"Error closing client for alias '{alias}': {e}")
        self._clients.clear()

    async def _get_client(self, alias: str) -> any:
        """[懒加载核心] 按需创建并缓存底层客户端连接。"""
        if alias not in self._configs:
            raise VectorEngineError(f"Configuration with alias '{alias}' not found.")
        
        # 线程/协程安全地检查和创建客户端
        async with self._client_locks[alias]:
            if alias not in self._clients:
                print(f"Client for '{alias}' not found in cache. Creating new connection...")
                config = self._configs[alias]
                
                if config.engine_type == 'milvus':
                    from pymilvus import MilvusClient
                    uri = f"http://{config.host}:{config.port}"
                    self._clients[alias] = MilvusClient(uri=uri, timeout=5)
                # elif config.engine_type == 'qdrant':
                #     from qdrant_client import QdrantClient
                #     self._clients[alias] = QdrantClient(host=config.host, port=config.port)
                else:
                    raise NotImplementedError(f"Engine type '{config.engine_type}' is not supported.")
                print(f"Successfully created and cached client for '{alias}'.")

        return self._clients[alias]

    async def get_engine(self, alias: str) -> VectorEngineService:
        """
        [工厂方法]
        获取一个配置好的、可用的 VectorEngineService 实例。
        这是应用层（依赖项、Worker）应该调用的唯一方法。
        """
        config = self._configs.get(alias)
        if not config:
            raise VectorEngineError(f"Configuration with alias '{alias}' not found.")
            
        client = await self._get_client(alias)
        
        if config.engine_type == 'milvus':
            return MilvusEngine(client=client)
        # elif config.engine_type == 'qdrant':
        #     return QdrantEngine(client=client)
        else:
            raise NotImplementedError(f"Engine type '{config.engine_type}' is not supported.")