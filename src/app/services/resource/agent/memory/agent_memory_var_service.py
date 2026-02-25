import json
from typing import List, Dict, Any, Optional
from sqlalchemy import select, and_, or_
from app.core.context import AppContext
from app.services.base_service import BaseService
from app.dao.resource.agent.agent_memory_dao import AgentMemoryVarDao, AgentMemoryVarValueDao
from app.models.resource.agent.agent_memory import AgentMemoryVar, AgentMemoryVarValue, MemoryScope, MemoryType
from app.schemas.resource.agent.agent_memory_schemas import AgentMemoryVarCreate, AgentMemoryVarUpdate, AgentMemoryVarRead
from app.services.exceptions import ServiceException, NotFoundError

class AgentMemoryVarService(BaseService):
    def __init__(self, context: AppContext):
        self.db = context.db
        self.dao = AgentMemoryVarDao(context.db)
        self.value_dao = AgentMemoryVarValueDao(context.db)

    # ==========================
    # Management Methods (Schema)
    # ==========================
    
    async def list_memories(self, agent_id: int) -> List[AgentMemoryVarRead]:
        """获取 Agent 的所有记忆变量定义"""
        memories = await self.dao.get_list(where={"agent_id": agent_id})
        return [AgentMemoryVarRead.model_validate(m) for m in memories]

    async def create_memory(self, agent_id: int, data: AgentMemoryVarCreate) -> AgentMemoryVarRead:
        """创建新的记忆变量"""
        # 1. 检查 Key 重复
        if await self.dao.get_by_agent_and_key(agent_id, data.key):
            raise ServiceException(f"Memory key '{data.key}' already exists.")
        
        # 2. 序列化默认值
        default_val_str = self._serialize_value(data.default_value) if data.default_value is not None else None
        
        memory = AgentMemoryVar(
            agent_id=agent_id,
            key=data.key,
            label=data.label,
            type=data.type,
            scope_type=data.scope_type,
            description=data.description,
            default_value=default_val_str,
            is_active=data.is_active
        )
        await self.dao.add(memory)
        return AgentMemoryVarRead.model_validate(memory)

    async def update_memory(self, agent_id: int, memory_id: int, data: AgentMemoryVarUpdate) -> AgentMemoryVarRead:
        memory = await self.dao.get_by_pk(memory_id)
        if not memory or memory.agent_id != agent_id:
            raise NotFoundError("Memory variable not found.")
            
        update_dict = data.model_dump(exclude_unset=True)
        if 'default_value' in update_dict:
            update_dict['default_value'] = self._serialize_value(update_dict['default_value'])
            
        for k, v in update_dict.items():
            setattr(memory, k, v)
            
        await self.db.flush()
        return AgentMemoryVarRead.model_validate(memory)

    async def delete_memory(self, agent_id: int, memory_id: int):
        memory = await self.dao.get_by_pk(memory_id)
        if not memory or memory.agent_id != agent_id:
            raise NotFoundError("Memory variable not found.")
        # 级联删除 Value (数据库外键已处理，但代码层显式调用更安全)
        await self.dao.delete(memory)

    # ==========================
    # Runtime Methods (Value)
    # ==========================

    async def get_runtime_object(
        self, 
        agent_id: int, 
        user_id: int, 
        session_uuid: Optional[str]
    ) -> Dict[str, Any]:
        """
        [Performance Core]
        一次性获取该 Agent 在当前 User/Session 下所有记忆变量的运行时值。
        
        流程：
        1. 获取该 Agent 定义的所有 Active Memory Schema。
        2. 批量查询这些 Memory ID 对应的 Value 记录 (根据 UserID 和 SessionUUID)。
        3. 在内存中进行 Merge：Schema Default -> Runtime Value。
        
        Returns:
            Dict[str, Any]: { "memory_key": "final_value", ... }
        """
        # 1. 获取所有定义
        memories_schema = await self.dao.get_list(where={"agent_id": agent_id, "is_active": True})
        if not memories_schema:
            return {}

        memory_map = {m.id: m for m in memories_schema}
        memory_ids = list(memory_map.keys())

        # 2. 批量查询值
        # 条件：memory_id IN (...) AND ( (user_id=...) OR (session_uuid=...) )
        # 注意：这里我们查出所有相关的 value，然后在内存里匹配 scope
        stmt = select(AgentMemoryVarValue).where(
            and_(
                AgentMemoryVarValue.memory_id.in_(memory_ids),
                or_(
                    AgentMemoryVarValue.user_id == user_id,
                    AgentMemoryVarValue.session_uuid == session_uuid
                )
            )
        )
        result = await self.db.execute(stmt)
        values_records = result.scalars().all()

        # 构建 (memory_id, scope_type) -> value 的临时映射
        # 注意：Value表里没有 scope_type 字段，是通过 user_id/session_uuid 区分的
        # 但我们知道定义的 scope_type，所以可以匹配
        values_map = {} # { memory_id: value_record }
        
        for val in values_records:
            # 简单的冲突解决：如果同一个 memory_id 有多条记录（理论上受约束限制不应发生，除非脏数据），
            # 优先取 session 级的? 不，Schema 决定了 scope，所以一个 memory_id 在特定 context 下只能有一条有效 value。
            values_map[val.memory_id] = val

        # 3. 组装结果
        runtime_obj = {}
        for m in memories_schema:
            val_record = values_map.get(m.id)
            
            final_val = None
            
            # 尝试从运行时记录获取
            if val_record:
                # 双重检查：确保查到的 value 符合 schema 定义的 scope
                # 例如：Schema 是 USER，但查到了 session_uuid 有值的记录（脏数据），应忽略或处理
                if m.scope_type == MemoryScope.USER and val_record.user_id == user_id:
                    final_val = self._deserialize_value(val_record.value, m.type)
                elif m.scope_type == MemoryScope.SESSION and val_record.session_uuid == session_uuid:
                    final_val = self._deserialize_value(val_record.value, m.type)
            
            # 如果运行时没有值，使用默认值
            if final_val is None and m.default_value is not None:
                final_val = self._deserialize_value(m.default_value, m.type)
            
            # 如果还是 None，设为空字符串或特定类型的空值，防止模板渲染报错
            if final_val is None:
                final_val = ""
                
            runtime_obj[m.key] = final_val

        return runtime_obj
        
    async def get_runtime_value(self, agent_id: int, key: str, user_id: int, session_uuid: str) -> Any:
        """
        [LLM 读接口] 获取记忆值。
        逻辑：查 Schema -> 确定 Scope -> 查 Value 表 -> 反序列化 -> 若无则返回 Default
        """
        # 1. 获取定义
        # 这里应该加 Redis 缓存，因为 Schema 是读多写少
        memory_def = await self.dao.get_by_agent_and_key(agent_id, key)
        if not memory_def or not memory_def.is_active:
            return None # 或者抛错，视业务而定

        # 2. 确定查询条件
        val_record = None
        if memory_def.scope_type == MemoryScope.USER:
            val_record = await self.value_dao.get_value(memory_def.id, user_id=user_id)
        elif memory_def.scope_type == MemoryScope.SESSION:
            val_record = await self.value_dao.get_value(memory_def.id, session_uuid=session_uuid)
            
        # 3. 返回值处理
        if val_record and val_record.value is not None:
            return self._deserialize_value(val_record.value, memory_def.type)
        
        # 4. 回退到默认值
        if memory_def.default_value is not None:
            return self._deserialize_value(memory_def.default_value, memory_def.type)
            
        return None

    async def set_runtime_value(self, agent_id: int, key: str, value: Any, user_id: int, session_uuid: str):
        """
        [LLM 写接口] 设置记忆值。
        """
        memory_def = await self.dao.get_by_agent_and_key(agent_id, key)
        if not memory_def:
            raise NotFoundError(f"Memory variable '{key}' is not defined.")
            
        # 序列化
        val_str = self._serialize_value(value)
        
        # Upsert 逻辑
        if memory_def.scope_type == MemoryScope.USER:
            criteria = {"memory_id": memory_def.id, "user_id": user_id}
        else:
            criteria = {"memory_id": memory_def.id, "session_uuid": session_uuid}
            
        existing = await self.value_dao.get_one(where=criteria)
        if existing:
            existing.value = val_str
        else:
            new_val = AgentMemoryVarValue(**criteria, value=val_str)
            await self.value_dao.add(new_val)

    # --- Helpers ---
    def _serialize_value(self, value: Any) -> str:
        if value is None: return None
        if isinstance(value, (dict, list, bool, int, float)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _deserialize_value(self, val_str: str, type_enum: MemoryType) -> Any:
        if val_str is None: return None
        try:
            if type_enum in [MemoryType.JSON, MemoryType.LIST, MemoryType.BOOLEAN, MemoryType.NUMBER]:
                return json.loads(val_str)
            return val_str # STRING
        except:
            return val_str # Fallback