# src/app/services/resource/agent/memory/deep/long_term_context_service.py

import logging
import json
from typing import List, Dict, Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AppContext
from app.services.base_service import BaseService
from app.models import User, Team, Workspace, ChatMessage, MessageRole
from app.models.resource.agent import Agent
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.dao.interaction.chat_dao import ChatMessageDao
from app.dao.resource.resource_dao import ResourceInstanceDao
from app.dao.module.service_module_dao import ServiceModuleVersionDao
from app.services.module.embedding_service import EmbeddingService
from app.system.vectordb.constants import AGENT_LONG_TERM_CONTEXT_COLLECTION
from app.services.exceptions import ConfigurationError, ServiceException, NotFoundError
from app.schemas.resource.agent.agent_schemas import DeepMemoryConfig

# 引入 Parsing 引擎相关
from app.engine.vector.base import VectorChunk
from app.engine.parsing.base import Document, ChunkerPolicy
from app.engine.parsing.chunkers.context_chunker import ContextChunker

logger = logging.getLogger(__name__)

# 系统级长期记忆集合名称
COLLECTION_NAME = AGENT_LONG_TERM_CONTEXT_COLLECTION

class LongTermContextService(BaseService):
    """
    [Deep Memory Layer 1] 长期上下文服务 (LongTermContextService).
    
    职责：
    1. Indexing (后台): 将完整对话轮次 (Trace) 切分并向量化存入系统集合。
    2. Retrieval (运行时): 基于语义相似度，召回当前会话中早期的完整轮次。
    
    设计原则：
    - 严格的 Session 隔离：防止跨会话污染。
    - 完整性：通过 Chunking 确保长文本也能被完整索引，命中任意 Chunk 即召回整轮。
    - 计费明确：索引成本归属于 Agent 所在的 Workspace。
    """
    
    def __init__(self, context: AppContext):
        self.db = context.db
        self.workspace_dao = WorkspaceDao(context.db)
        self.message_dao = ChatMessageDao(context.db)
        self.instance_dao = ResourceInstanceDao(context.db)
        self.smv_dao = ServiceModuleVersionDao(context.db)
        self.embedding_service = EmbeddingService(context)
        self.vector_manager = context.vector_manager
        
        # 实例化 Chunker (它无状态且轻量)
        self.chunker = ContextChunker()

    def _build_turn_text(self, messages: List[ChatMessage]) -> str:
        """
        [Content Assembly] 将一个完整的对话轮次拼装成一个长文本。
        我们保留所有内容，具体的 Token 限制交由 Chunker 处理。
        """
        buffer = []
        for msg in messages:
            role_tag = msg.role.value.upper()
            content = msg.content or ""
            
            # 特殊处理 Tool Calls 的显示，增加语义可读性
            if msg.role == MessageRole.ASSISTANT and msg.tool_calls:
                try:
                    calls = msg.tool_calls if isinstance(msg.tool_calls, list) else json.loads(msg.tool_calls)
                    func_names = [c.get('function', {}).get('name', 'unknown') for c in calls]
                    content += f"\n[Action: Calling tools: {', '.join(func_names)}]"
                except Exception:
                    pass
            
            buffer.append(f"<{role_tag}>\n{content}\n</{role_tag}>")
        
        return "\n\n".join(buffer)

    async def index_turn_background(
        self, 
        agent_instance_id: int,
        session_uuid: str,
        trace_id: str, 
        messages: List[ChatMessage],
        # 显式传入运行时工作空间ID，用于计费归属
        runtime_workspace_id: int 
    ):
        """
        [Worker Task] 后台任务：索引一个对话轮次。
        这是真正的生产级实现，包含完整的上下文加载、切分、计费和入库。
        """
        if not messages: return

        # 1. 获取系统默认 Embedding 模型
        default_emb_module = await self.smv_dao.get_default_version_by_type("embedding")
        if not default_emb_module:
            logger.error("No default embedding module configured. Cannot index long-term context.")
            return

        # 2. 补全上下文
        agent_instance = await self.instance_dao.get_by_pk(agent_instance_id)
        if not agent_instance:
            logger.warning(f"Agent instance {agent_instance_id} not found. Skipping indexing.")
            return

        runtime_workspace = await self.workspace_dao.get_by_pk(runtime_workspace_id)

        if not runtime_workspace:
            # 严重错误：运行时环境已消失，无法计费，任务中止
            return

        # 3. 构建并切分文本
        raw_text = self._build_turn_text(messages)
        if not raw_text.strip():
            return

        # 使用 Embedding 模型的 max_batch_tokens 作为参考，或者给一个安全值
        # 通常 Embedding 模型单条限制在 8191 (OpenAI) 或 2048/4096 (其他)
        # 我们保守取 1024 或 2048 作为 Chunk Size
        max_tokens = default_emb_module.attributes.get("max_batch_tokens", 2048)
        # 安全起见，Chunk 不应超过 max_tokens，这里我们取 max_tokens 的一半作为 target chunk size
        # 留出余量给 payload 或系统 prompt
        target_chunk_size = min(max_tokens, 1024) 

        doc = Document(
            content=raw_text, 
            content_type="text", 
            mime_type="text/plain", 
            source_parser="agent_context", 
            metadata={}
        )
        
        chunks = await self.chunker.run(doc, max_tokens=target_chunk_size, overlap_tokens=50)
        
        if not chunks:
            return

        try:
            # 4. 确保集合存在
            dims = default_emb_module.attributes.get("dimensions")

            # 5. 生成向量 (Batch Embedding + Billing)
            texts_to_embed = [c.content for c in chunks]
            
            emb_result = await self.embedding_service.generate_embedding(
                module_version_id=default_emb_module.id,
                workspace=runtime_workspace,
                texts=texts_to_embed
            )
            
            # 6. 组装 Vector Chunks
            vector_chunks_to_upsert = []
            for i, res in enumerate(emb_result.results):
                if not res.vector:
                    continue
                
                # 为每个 Chunk 生成唯一 ID，但 Payload 里共享 trace_id
                # 这样检索时可以通过 payload.trace_id 聚合
                chunk_id = f"{trace_id}_chunk_{i}"
                
                vector_chunks_to_upsert.append(VectorChunk(
                    id=chunk_id,
                    vector=res.vector,
                    payload={
                        "agent_instance_id": agent_instance_id,
                        "session_uuid": session_uuid,
                        "trace_id": trace_id,
                        # 仅保留该 Chunk 的预览，而不是整轮文本
                        "content_preview": texts_to_embed[i][:100],
                        "chunk_index": i
                    }
                ))

            # 7. Upsert
            if vector_chunks_to_upsert:
                engine = await self.vector_manager.get_engine("default")
                await engine.upsert(COLLECTION_NAME, vector_chunks_to_upsert)
                logger.info(f"Indexed {len(vector_chunks_to_upsert)} chunks for trace {trace_id}")

        except Exception as e:
            logger.error(f"Failed to index long-term context for trace {trace_id}: {e}", exc_info=True)

    async def retrieve(
        self, 
        query: str, 
        agent_instance_id: int,
        session_uuid: str,
        exclude_trace_ids: List[str], 
        deep_memory_config: DeepMemoryConfig,
        runtime_workspace: Workspace
    ) -> List[List[ChatMessage]]:
        """
        [Runtime Retrieval] 检索长期记忆。
        
        逻辑：
        1. 确定搜索范围 (必须是当前 Session)。
        2. 生成 Query 向量 (Cached)。
        3. 向量搜索 (Oversampling)。
        4. 聚合 Trace ID 并去重。
        5. DB 反查完整轮次。
        
        Returns:
            List[List[ChatMessage]]: 返回一个列表，每个元素是一个完整轮次的消息列表。
        """

        if not runtime_workspace:
            # 严重错误：运行时环境已消失，无法计费，任务中止
            return []

        if not deep_memory_config.enabled or not query:
            return []

        # 强制检查：LongTermContext 仅支持 Session 级别
        # 即使 Config 里写了 user，对于 Context 召回我们也应该限制在 Session 内
        # (根据最新的架构决策)
        # 如果未来确实需要跨 Session 召回 Context，可以放开，但目前严格限制
        
        default_emb_module = await self.smv_dao.get_default_version_by_type("embedding")
        if not default_emb_module:
            return []

        try:
            # 1. 生成 Query 向量
            
            emb_result = await self.embedding_service.generate_embedding(
                module_version_id=default_emb_module.id,
                workspace=runtime_workspace,
                texts=[query]
            )
            
            if not emb_result.results or not emb_result.results[0].vector:
                return []
            
            query_vector = emb_result.results[0].vector

            # 2. 向量搜索
            engine = await self.vector_manager.get_engine("default")
            
            # Filter: 必须匹配 Agent, User 和 Session
            filter_expr = (
                f'payload["agent_instance_id"] == {agent_instance_id} '
                f'&& payload["session_uuid"] == "{session_uuid}"'
            )
            
            # Oversampling Strategy
            # 我们需要 N 个不同的 trace_id。假设平均每轮被切分为 5 个 chunk。
            # 设定 limit = target * 10 以确保足够召回
            target_count = deep_memory_config.max_recall_turns
            search_limit = target_count * 10
            
            # 安全上限，防止查太多
            search_limit = min(search_limit, 100) 
            
            search_results = await engine.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                top_k=search_limit,
                filter_expr=filter_expr
            )

            # 3. 聚合 Trace ID (去重与过滤)
            valid_trace_ids = []
            seen_traces = set(exclude_trace_ids) # 预先排除短期记忆中的 traces
            
            for res in search_results:
                if len(valid_trace_ids) >= target_count:
                    break
                
                # 相似度阈值过滤
                if res.score < deep_memory_config.min_match_score:
                    continue
                
                tid = res.payload.get("trace_id")
                if tid and tid not in seen_traces:
                    valid_trace_ids.append(tid)
                    seen_traces.add(tid)

            if not valid_trace_ids:
                return []

            # 4. 数据库反查 (Hydration)
            # 我们需要按 Trace 分组返回，以便上层 Processor 可以按轮次组织 Prompt
            
            # 查出所有消息
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.trace_id.in_(valid_trace_ids))
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            result = await self.db.execute(stmt)
            all_messages = result.scalars().all()
            
            # 按 Trace ID 分组
            messages_by_trace: Dict[str, List[ChatMessage]] = {tid: [] for tid in valid_trace_ids}
            for msg in all_messages:
                if msg.trace_id in messages_by_trace:
                    messages_by_trace[msg.trace_id].append(msg)
            
            # 按 valid_trace_ids 的顺序（即相关性顺序）返回
            # 或者按时间倒序？通常 RAG 倾向于相关性，但对话历史倾向于时间。
            # 考虑到这是“回忆”，我们保持相关性顺序，或者让上层决定。
            # 这里我们返回相关性顺序的轮次列表。
            ordered_turns = []
            for tid in valid_trace_ids:
                turn_msgs = messages_by_trace.get(tid)
                if turn_msgs:
                    ordered_turns.append(turn_msgs)
            
            logger.info(f"LongTermContext: Recalled {len(ordered_turns)} turns for query: {query[:20]}")
            return ordered_turns

        except Exception as e:
            # 容错：深度记忆检索失败不应阻塞主对话
            logger.error(f"LongTermContext retrieval failed: {e}", exc_info=True)
            return []

    async def retrieve_by_trace_id_direct(self, trace_id: str) -> List[ChatMessage]:
        """
        [Tool Support] 根据 Trace ID 精确找回某一轮对话的完整内容。
        用于 'expand_long_term_context' 工具。
        """
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.trace_id == trace_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()