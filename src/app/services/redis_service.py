# src/app/services/redis_service.py

import json
import logging
from pathlib import Path
from typing import List, Any, Optional, Dict
import redis.asyncio as aioredis
import redis.exceptions
from datetime import timedelta
from app.core.config import settings

class LuaScriptManager:
    """A manager to load, register, and execute Lua scripts from files."""
    def __init__(self, client: aioredis.Redis, script_dir: Path):
        self.client = client
        self.script_dir = script_dir
        self._sha_cache: Dict[str, str] = {} # Cache for script name -> SHA hash

    async def load_and_register_scripts(self):
        """
        Scans the script directory, loads each .lua file, and registers it with Redis.
        This should be called once when the RedisService is initialized.
        """
        if not self.script_dir.is_dir():
            logging.warning(f"Lua script directory not found: {self.script_dir}")
            return

        for script_file in self.script_dir.glob("*.lua"):
            script_name = script_file.stem  # e.g., "reserve_feature"
            try:
                with open(script_file, 'r', encoding='utf-8') as f:
                    script_content = f.read()
                # SCRIPT LOAD tells Redis to compile and cache the script, returning its SHA hash.
                sha_hash = await self.client.script_load(script_content)
                self._sha_cache[script_name] = sha_hash
                logging.info(f"Successfully loaded and registered Lua script '{script_name}' with SHA: {sha_hash[:10]}...")
            except Exception as e:
                logging.error(f"Failed to load Lua script '{script_name}': {e}", exc_info=True)
                # In a production system, you might want this to be a fatal error that stops startup.
                raise

    async def execute(self, script_name: str, keys: List[str] = None, args: List[Any] = None) -> Any:
        """
        Executes a pre-loaded script by its SHA hash for maximum performance.
        Falls back to EVAL if the script is not in Redis's cache (e.g., after a server flush).
        """
        sha_hash = self._sha_cache.get(script_name)
        if not sha_hash:
            raise RuntimeError(f"Lua script '{script_name}' is not loaded. Ensure load_and_register_scripts() was called.")

        try:
            # EVALSHA is the preferred, high-performance way to run scripts.
            return await self.client.evalsha(sha_hash, len(keys or []), *(keys or []), *(args or []))
        except redis.exceptions.NoScriptError:
            # This is a rare edge case where Redis might have flushed its script cache.
            # We handle it gracefully by reloading and retrying once.
            logging.warning(f"Lua script '{script_name}' (SHA: {sha_hash[:10]}) not found in Redis cache. Reloading...")
            await self.load_and_register_scripts()
            # Retry the call
            new_sha_hash = self._sha_cache.get(script_name)
            if not new_sha_hash:
                 raise RuntimeError(f"Failed to reload Lua script '{script_name}' after NoScriptError.")
            return await self.client.evalsha(new_sha_hash, len(keys or []), *(keys or []), *(args or []))
        except Exception as e:
            # Handle other potential Redis errors
            logging.error(f"Error executing Lua script '{script_name}': {e}", exc_info=True)
            raise

class RedisService:
    """
    一个封装了 aioredis 客户端的通用服务，提供了应用层面的常用方法。
    """
    def __init__(self, client: aioredis.Redis=None):
        self.client = client if client else aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        # Define the path to our scripts directory
        script_path = Path(__file__).parent.parent / "scripts/redis"
        self.script_manager = LuaScriptManager(self.client, script_path)

    async def initialize(self):
        """Loads and registers all Lua scripts."""
        await self.script_manager.load_and_register_scripts()

    async def execute_lua_script(self, script_name: str, keys: List[str] = None, args: List[Any] = None) -> Any:
        return await self.script_manager.execute(script_name, keys, args)

    async def close(self):
        await self.client.aclose()

    async def set_json(self, key: str, data: Any, expire: Optional[timedelta] = None):
        """
        将 Python 对象序列化为 JSON 并存入 Redis。
        
        :param key: Redis 键。
        :param data: 任何可被 json.dumps 序列化的 Python 对象。
        :param expire: 可选的过期时间 (timedelta)。
        """
        value = json.dumps(data)
        await self.client.set(key, value, ex=expire)

    async def get_json(self, key: str) -> Optional[Any]:
        """
        从 Redis 获取一个键，并将其 JSON 值反序列化为 Python 对象。
        
        :param key: Redis 键。
        :return: Python 对象，如果键不存在则返回 None。
        """
        value = await self.client.get(key)
        if value:
            return json.loads(value)
        return None

    async def delete_key(self, key: str) -> int:
        """
        删除一个或多个键。
        
        :param key: 要删除的键。
        :return: 被删除键的数量。
        """
        return await self.client.delete(key)

    async def delete_by_prefix(self, prefix: str, max_retries: int = 2, retry_delay: float = 0.2, batch_size: int = 500):
        """
        [ULTIMATE & HARDENED] Safely deletes keys by prefix with verification, 
        batching, and non-blocking operations.
        """
        # Prefer UNLINK for non-blocking deletion
        delete_cmd = self.client.unlink if hasattr(self.client, 'unlink') else self.client.delete
        
        total_deleted = 0
        attempt = 0
        
        while attempt <= max_retries:
            try:
                # PHASE 1: Batch deletion
                cursor = 0
                batch_count = 0
                match_pattern = f"{prefix}*" if not prefix.endswith('*') else prefix
                
                while True:
                    cursor, keys = await self.client.scan(
                        cursor, 
                        match=match_pattern, 
                        count=batch_size
                    )
                    
                    if keys:
                        await delete_cmd(*keys)
                        batch_count += len(keys)
                        total_deleted += len(keys)
                    
                    if cursor == 0:
                        break
                
                if batch_count == 0:
                    logging.info(f"No keys found with prefix '{prefix}'")
                    return total_deleted
                
                logging.info(f"Deleted {batch_count} keys in attempt {attempt+1} for prefix '{prefix}'")
                
                # PHASE 2: Verification
                await asyncio.sleep(retry_delay)
                remaining_keys = await self._scan_keys(prefix)
                
                if not remaining_keys:
                    logging.info(f"Verified complete deletion of {total_deleted} keys for prefix '{prefix}'")
                    return total_deleted
                
                # Prepare for retry
                logging.warning(
                    f"Found {len(remaining_keys)} remaining keys after attempt {attempt+1} "
                    f"for prefix '{prefix}'. Sample: {remaining_keys[:3]}"
                )
                attempt += 1
                
            except (ConnectionError, TimeoutError) as e:
                if attempt >= max_retries:
                    logging.error(f"Final attempt failed for prefix '{prefix}': {str(e)}")
                    raise
                logging.warning(f"Network error during deletion (attempt {attempt+1}): {str(e)}")
                await asyncio.sleep(retry_delay * (attempt + 1))
                attempt += 1
                
            except Exception as e:
                logging.critical(
                    f"Critical error deleting prefix '{prefix}': {str(e)}", 
                    exc_info=True
                )
                raise
        
        # Final verification after all retries
        remaining_keys = await self._scan_keys(prefix)
        if remaining_keys:
            logging.critical(
                f"FAILED to delete all keys for prefix '{prefix}' after {max_retries} retries. "
                f"{len(remaining_keys)} keys remain. Sample: {remaining_keys[:5]}"
            )
        else:
            logging.info(f"Successfully deleted all keys after {max_retries} retries")
        
        return total_deleted

    async def _scan_keys(self, prefix: str, batch_size: int = 1000) -> List[str]:
        """Scans keys with batching to avoid memory issues"""
        keys_found = []
        cursor = 0
        match_pattern = f"{prefix}*" if not prefix.endswith('*') else prefix
        
        while True:
            cursor, keys = await self.client.scan(cursor, match=match_pattern, count=batch_size)
            keys_found.extend(keys)
            if cursor == 0:
                break
        return keys_found

    # 未来可以添加更多方法, e.g., get_list, push_to_list, etc.