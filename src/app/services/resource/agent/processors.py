import logging
import json
import asyncio
from typing import List, Dict, Any, Optional, Callable, Set, Union, Tuple
from dataclasses import dataclass, field

from app.core.context import AppContext
from app.models import User, Workspace, ResourceRef
from app.models.interaction.chat import ChatMessage
from app.models.resource.agent import AgentMemoryVar, AgentContextSummary
from app.models.resource.tool import Tool
from app.engine.model.llm import LLMMessage, LLMTool, LLMToolFunction
from app.engine.agent import BaseToolExecutor
from app.schemas.resource.agent.agent_schemas import AgentConfig, AgentRAGConfig, DeepMemoryConfig
from app.schemas.resource.knowledge.knowledge_schemas import RAGConfig, KnowledgeBaseExecutionRequest, KnowledgeBaseExecutionParams, SearchResultChunk
from app.schemas.resource.execution_schemas import AnyExecutionRequest
from app.services.exceptions import ServiceException, NotFoundError

# Lazy imports to avoid circular dependency loops during initialization
from app.services.resource.agent.agent_session_manager import AgentSessionManager
# Note: Specific services like LongTermContextService are imported inside methods or __init__ where safe

logger = logging.getLogger(__name__)

# ==============================================================================
# 1. Pipeline Context (KV Cache Optimized)
# ==============================================================================

@dataclass
class AgentPipelineContext:
    """
    Prompt 结构优化示意图 (KV Cache 友好型):
    [ Stable Area --------------------------------------- ] [ Volatile Area ]
    [ System Prompt ] + [ L2 Summaries ] + [ Short History ] + [ RAG/User Query ]
    """
    # 1. System (Stable)
    system_message: Optional[LLMMessage] = None
    
    # 2. System (Semi-Stable: 仅在截断发生时批量更新，周期内保持不变)
    system_contexts: List[str] = field(default_factory=list)
    
    # 3. History (Append-Only Stable: 周期内只追加不修改)
    history: List[LLMMessage] = field(default_factory=list)
    
    # 4. Dynamic (Volatile: 每次请求都不一样)
    dynamic_contexts: List[str] = field(default_factory=list)
    
    user_message: LLMMessage = field(default_factory=lambda: LLMMessage(role="user", content=""))
    
    # 状态追踪
    exclude_trace_ids: Set[str] = field(default_factory=set)

    def get_query_text(self) -> str:
        """从 user_message 中提取纯文本用于向量检索，兼容多模态结构。"""
        content = self.user_message.content
        if not content:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Handle OpenAI multi-modal format [{"type": "text", "text": "..."}]
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            return " ".join(text_parts)
        return ""

    def add_context_block(self, content: str):
        """添加检索到的上下文文本块"""
        if content:
            self.dynamic_contexts.append(content)

    def to_llm_messages(self) -> List[LLMMessage]:
        """
        [Final Assembly] 组装最终发给 LLM 的消息列表。
        """
        messages = []

        # 1. System Prompt
        final_system_msg = self._inject_context_into_system()
        if final_system_msg:
            messages.append(final_system_msg)
            
        # 2. History (Stable Prefix)
        if self.history:
            messages.extend(self.history)
        
        # 3. Dynamic Context Injection (Into User Message)
        final_user_msg = self._inject_context_into_query()
        messages.append(final_user_msg)
        
        return messages

    def _inject_context_into_system(self) -> Optional[LLMMessage]:
        """将 dynamic_contexts 注入到 user_message 中"""
        injection_text = ""
        final_system_msg = self.system_message
        if self.system_contexts:
            injection_text = "\n\n".join(self.system_contexts)
            if final_system_msg:
                final_system_msg.content = final_system_msg.content + f"\n{injection_text}"
            else:
                final_system_msg = LLMMessage(
                    role="system", 
                    content=injection_text
                )
        return final_system_msg

    def _inject_context_into_query(self) -> LLMMessage:
        """将 dynamic_contexts 注入到 user_message 中"""
        if not self.dynamic_contexts:
            return self.user_message

        # 构造上下文文本块
        context_block = "\n\n".join(self.dynamic_contexts)
        injection_text = f"### Relevant Context:\n{context_block}\n\n### Current Query:\n"
        
        final_user_msg = self.user_message.model_copy(deep=True)
        
        if isinstance(final_user_msg.content, str):
            final_user_msg.content = injection_text + final_user_msg.content
        elif isinstance(final_user_msg.content, list):
            # Insert text block at the beginning of multi-modal content
            context_item = {"type": "text", "text": injection_text}
            final_user_msg.content.insert(0, context_item)
            
        return final_user_msg

class BaseContextProcessor:
    """Processor 协议接口"""
    async def process(self, ctx: AgentPipelineContext):
        raise NotImplementedError

class BaseSkillProcessor:
    """Processor 协议接口"""
    async def process(self, tool_executor: BaseToolExecutor):
        raise NotImplementedError

# ==============================================================================
# 2. Tool Executor (Unified Local & Remote)
# ==============================================================================

class ResourceAwareToolExecutor(BaseToolExecutor):
    """
    [Universal Tool Container]
    统一管理“远程资源工具”（通过 ExecutionService 调用）和“本地系统工具”（Python 函数）。
    """
    def __init__(self, context: AppContext, runtime_workspace: Workspace):
        self.context = context
        self.runtime_workspace = runtime_workspace
        # Lazy import ExecutionService to avoid cycles
        from app.services.resource.execution.execution_service import ExecutionService
        self.execution_service = ExecutionService(context)
        
        self.local_functions: Dict[str, Callable] = {}
        self.resource_instances: Dict[str, str] = {} # tool_name -> instance_uuid
        self.client_side_tools: Set[str] = set()
        self.llm_tools_def: List[LLMTool] = []

    def register_local_function(self, tool_def: LLMTool, fn: Callable):
        """注册本地 Python 函数工具"""
        self.local_functions[tool_def.function.name] = fn
        self.llm_tools_def.append(tool_def)

    def register_resource_instance(self, tool_def: LLMTool, instance_uuid: str):
        """注册远程资源工具"""
        self.resource_instances[tool_def.function.name] = instance_uuid
        self.llm_tools_def.append(tool_def)

    def register_client_tool(self, tool_def: LLMTool):
        """注册由客户端执行并回传结果的工具定义。"""
        self.client_side_tools.add(tool_def.function.name)
        self.llm_tools_def.append(tool_def)

    def get_llm_tools(self) -> List[LLMTool]:
        return self.llm_tools_def

    def requires_client_execution(self, tool_name: str) -> bool:
        return tool_name in self.client_side_tools

    async def execute(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        """统一执行入口"""
        # 1. 优先检查本地函数 (Fast Path)
        if tool_name in self.local_functions:
            try:
                logger.debug(f"[ToolExecutor] Executing local function: {tool_name}")
                return await self.local_functions[tool_name](**tool_args)
            except Exception as e:
                logger.error(f"[ToolExecutor] Local function {tool_name} failed: {e}", exc_info=True)
                return {"error": f"Error executing local tool '{tool_name}': {str(e)}"}
        
        # 2. 检查资源实例 (Remote Path via ExecutionService)
        instance_uuid = self.resource_instances.get(tool_name)
        if instance_uuid:
            return await self._execute_remote_resource(tool_name, instance_uuid, tool_args)

        return {"error": f"Tool '{tool_name}' not found."}

    async def _execute_remote_resource(self, tool_name: str, instance_uuid: str, args: Dict[str, Any]) -> Any:
        request = AnyExecutionRequest(inputs=args)
        try:
            logger.info(f"[ToolExecutor] Calling remote resource: {tool_name} ({instance_uuid})")
            
            # 委托给 ExecutionService 处理 Trace 和 Billing
            result = await self.execution_service.execute_instance(
                instance_uuid=instance_uuid,
                execute_params=request,
                actor=self.context.actor,
                runtime_workspace=self.runtime_workspace
            )
            
            if not result.success:
                return {"error": result.error_message or "Unknown remote execution error"}
            
            return result.data
            
        except Exception as e:
            logger.error(f"[ToolExecutor] Remote tool {tool_name} failed: {e}", exc_info=True)
            return {"error": f"Tool execution failed: {str(e)}"}

# ==============================================================================
# 3. Skill Processor (Capability Builder)
# ==============================================================================

class DependencySkillsProcessor(BaseSkillProcessor):
    """
    [通用] 负责加载外部依赖资源作为工具。
    适用于 Agent 和 Workflow Node。
    """
    def __init__(self, context: AppContext, dependencies: List[ResourceRef]):
        self.context = context
        self.dependencies = dependencies
        
        # Lazy import to avoid circular dependency
        from app.services.resource.resource_service import ResourceService
        self.resource_service = ResourceService(context)

    async def process(self, tool_executor: BaseToolExecutor) -> None:
        for dep in self.dependencies:
            instance = dep.target_instance
            if not instance:
                continue

            instance_uuid = instance.uuid
            display_name = (
                dep.alias
                or (dep.target_resource.name if dep.target_resource else None)
                or instance_uuid
            )

            try:
                # 依赖关系里拿到的是轻量实例，先按 uuid 加载 full typed instance。
                full_instance, target_service = await self.resource_service._get_full_instance_and_service(instance_uuid)
                # 转换 Tool 定义
                tool_def = await target_service.as_llm_tool(full_instance)
                if tool_def:
                    # 使用引用中的 alias 重命名工具 (如果存在)
                    if dep.alias:
                        tool_def.function.name = dep.alias
                    
                    # 注册到 Executor (ResourceAwareToolExecutor 会处理远程调用逻辑)
                    tool_executor.register_resource_instance(tool_def, full_instance.uuid)
            except Exception as e:
                logger.warning(f"Failed to load tool dependency {display_name}: {e}")

class DeepMemorySkillsProcessor(BaseSkillProcessor):
    """
    [Agent专用] 负责加载长期记忆扩展工具。
    允许模型通过 ID 反查详细的对话历史片段。
    """
    def __init__(self, context: AppContext, config: DeepMemoryConfig):
        self.context = context
        self.config = config
        
        from app.services.resource.agent.memory.deep.long_term_context_service import LongTermContextService
        self.long_term_service = LongTermContextService(context)

    async def process(self, tool_executor: BaseToolExecutor) -> None:
        if not self.config.enabled or not self.config.enable_summarization:
            return

        # 定义本地函数
        async def expand_context_fn(context_id: str):
            msgs = await self.long_term_service.retrieve_by_trace_id_direct(context_id)
            if not msgs:
                return "Context not found or expired."
            return "\n".join([f"{m.role.value}: {m.content}" for m in msgs])
        
        # 定义 Tool Schema
        tool_def = LLMTool(
            type="function",
            function=LLMToolFunction(
                name="expand_long_term_context",
                description="Retrieve full conversation details for a specific past turn using its Context ID.",
                parameters={
                    "type": "object", 
                    "properties": {"context_id": {"type": "string"}}, 
                    "required": ["context_id"]
                }
            )
        )
        
        # 注册本地函数
        tool_executor.register_local_function(tool_def, expand_context_fn)

class MemoryVarSkillsProcessor(BaseSkillProcessor):
    """
    [Session专用] 负责加载记忆变量读写工具。
    依赖于 Session Manager 的运行时状态。
    """
    def __init__(self, context: AppContext, session_manager: AgentSessionManager):
        self.context = context
        self.session_manager = session_manager
        
        from app.services.resource.agent.memory.agent_memory_var_service import AgentMemoryVarService
        self.memory_var_service = AgentMemoryVarService(context)

    async def process(self, tool_executor: BaseToolExecutor) -> None:
        if not self.session_manager or not self.session_manager.session:
            return

        agent_id = self.session_manager.agent_instance.id
        user_id = self.context.actor.id
        session_uuid = self.session_manager.session.uuid

        """
        暂时不需要get，因为我们通常注入到提示词中
        # --- Tool: memory_get ---
        async def memory_get(key: str):
            val = await self.memory_var_service.get_runtime_value(
                agent_id, key, user_id, session_uuid
            )
            return val if val is not None else "Variable not found."

        tool_executor.register_local_function(
            LLMTool(
                type="function",
                function=LLMToolFunction(
                    name="memory_get",
                    description="Get the value of a specific memory variable.",
                    parameters={
                        "type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]
                    }
                )
            ),
            memory_get
        )
        """

        # --- Tool: memory_set ---
        async def memory_set(key: str, value: Any):
            try:
                await self.memory_var_service.set_runtime_value(
                    agent_id, key, value, user_id, session_uuid
                )
                return "Memory updated successfully."
            except Exception as e:
                return f"Failed to update memory: {str(e)}"

        tool_executor.register_local_function(
            LLMTool(
                type="function",
                function=LLMToolFunction(
                    name="memory_set",
                    description="Set or update the value of a specific memory variable.",
                    parameters={
                        "type": "object", 
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string", "description": "JSON string or value"}
                        }, 
                        "required": ["key", "value"]
                    }
                )
            ),
            memory_set
        )

# ==============================================================================
# 4. Context Processors (Pipeline Nodes)
# ==============================================================================

class ShortContextProcessor(BaseContextProcessor):
    def __init__(
        self,
        context: AppContext,
        session_manager: Optional[AgentSessionManager],
        max_turns: int,
        # 截断保留比例 (默认 0.5)。例如 max=30, min=15
        min_turns_ratio: float = 0.5 
    ):
        self.context = context
        self.session_manager = session_manager
        # 将 "轮次(Turns)" 转换为 "消息数(Messages)"，通常 1 Turn = 2 Msgs (User+Assistant)
        # 这里为了精确控制，我们直接按 Message 数量计算
        self.max_msgs = max_turns * 2 
        self.min_msgs = max(2, int(self.max_msgs * min_turns_ratio))

    async def process(self, ctx: AgentPipelineContext) -> None:
        if not self.session_manager or not self.session_manager.session:
            return

        session = self.session_manager.session
        total_count = session.message_count

        # --- [核心算法：锯齿窗口计算] ---
        fetch_limit = 0
        
        if total_count <= self.max_msgs:
            # 阶段 A: 积累期。尚未触达上限，全量加载。
            # Cache 状态: Prefix Hit (Append 模式)
            fetch_limit = total_count
        else:
            # 阶段 B: 循环截断期。
            # 周期长度 = Max - Min
            cycle_len = self.max_msgs - self.min_msgs
            
            # 计算当前溢出量
            overflow = total_count - self.min_msgs
            
            # 计算当前周期内的偏移量
            # remainder == 0 意味着刚触发截断的那一刻 (回退到 Min)
            remainder = overflow % cycle_len
            
            # 目标窗口大小 = 基底 + 偏移量
            fetch_limit = self.min_msgs + remainder
            
            # 防御性上限
            fetch_limit = min(fetch_limit, self.max_msgs)

        if fetch_limit <= 0:
            return

        # --- [数据加载] ---
        # 务必保证 fetch_limit 是针对 "最近的消息"
        recent_msgs = await self.session_manager.session_service.get_recent_messages(
            session.id, 
            limit=int(fetch_limit)
        )
        
        # --- [构建上下文] ---
        llm_msgs = []
        trace_ids = set()
        
        for m in recent_msgs:
            if m.trace_id:
                trace_ids.add(m.trace_id)
                
            llm_msgs.append(LLMMessage(
                role=m.role.value,
                content=m.content,
                tool_calls=m.tool_calls,
                tool_call_id=m.tool_call_id
            ))
            
        ctx.history = llm_msgs
        ctx.exclude_trace_ids.update(trace_ids)

class RAGContextProcessor(BaseContextProcessor):
    """
    [Production Optimized] 负责执行向量检索 (RAG)。
    支持 'always' (批量全搜) 和 'auto' (智能路由 + 配置感知批量执行)。
    
    Highlights:
    1. AI Router for intent detection.
    2. Dynamic grouping by retrieval parameters (RAGConfig) for IO efficiency.
    3. Post-retrieval remapping for behavioral customization (no_recall_reply).
    """
    def __init__(
        self, 
        context: AppContext, 
        dependencies: List[ResourceRef], 
        rag_config: AgentRAGConfig, 
        runtime_workspace: Workspace
    ):
        self.context = context
        self.dependencies = dependencies
        self.config = rag_config
        self.runtime_workspace = runtime_workspace
        
        # Lazy import services to avoid circular deps
        from app.services.resource.execution.execution_service import ExecutionService
        self.execution_service = ExecutionService(context)
        
        from app.services.common.llm_capability_provider import AICapabilityProvider
        self.ai_provider = AICapabilityProvider(context)

    async def process(self, ctx: AgentPipelineContext) -> None:
        query_text = ctx.get_query_text()
        if not query_text.strip():
            return

        # 1. 过滤出 KnowledgeBase 类型的依赖
        kb_refs = [
            dep for dep in self.dependencies 
            if dep.target_instance and dep.target_instance.resource_type == 'knowledge'
        ]
        
        if not kb_refs:
            return

        try:
            rag_context_blocks = []
            
            # 2. 策略分发
            if self.config.call_method == "always":
                # [Always Mode] 强制使用全局配置，作为一个大批次处理，忽略个别差异以追求速度和统一性
                block = await self._execute_search_batch_global(query_text, [ref.target_instance.uuid for ref in kb_refs], self.config)
                if block: rag_context_blocks.append(block)
            else:
                # [Auto Mode] 智能路由 -> 参数分组 -> 批量执行 -> 行为重映射
                block = await self.as_need_search(query_text, kb_refs)
                if block: rag_context_blocks.append(block)
            
            # 3. 注入上下文
            if rag_context_blocks:
                # 使用分割线明确区分 RAG 内容
                full_rag_text = "\n".join(rag_context_blocks)
                ctx.add_context_block(full_rag_text)
                
        except Exception as e:
            logger.error(f"[RAGProcessor] Search process failed: {e}", exc_info=True)

    async def as_need_search(self, query_text: str, kb_refs: List[ResourceRef]) -> str:
        """
        [生产级智能按需模式]
        流程: AI筛选 -> 提取配置 -> 按检索参数分组 -> 并发检索 -> 结果重映射 -> 应用行为配置
        """
        # 1. AI 路由筛选
        selected_uuids = await self.call_agent_filter(query_text, kb_refs)
        
        if not selected_uuids:
            logger.debug(f"[RAG] Selector chose 0 KBs.")
            return ""

        # 2. 准备数据映射
        ref_map = {ref.target_instance.uuid: ref for ref in kb_refs}
        instance_config_map: Dict[str, AgentRAGConfig] = {}
        
        selected_refs = []
        for uid in selected_uuids:
            if uid in ref_map:
                ref = ref_map[uid]
                selected_refs.append(ref)
                # 预计算最终配置 (Global + Ref Options)
                instance_config_map[uid] = self._merge_config(self.config, ref.options)

        # 3. [核心优化] 基于 RAGConfig (纯检索参数) 分组
        # 目的: 将 no_recall_reply 不同但检索参数相同的请求合并
        batch_groups = self._group_refs_by_retrieval_params(selected_refs, instance_config_map)
        
        # 4. 并发执行检索 (返回原始数据)
        search_tasks = []
        for _, (rag_config, batch_uuids) in batch_groups.items():
            search_tasks.append(
                self._execute_search_batch_raw(query_text, batch_uuids, rag_config)
            )
        
        # Returns List[List[GroupedSearchResult]]
        batch_results_list = await asyncio.gather(*search_tasks, return_exceptions=True)
        
        # 5. 结果扁平化索引
        results_by_uuid: Dict[str, List[SearchResultChunk]] = {}
        for batch_res in batch_results_list:
            if isinstance(batch_res, Exception):
                logger.error(f"[RAG] Batch execution failed: {batch_res}", exc_info=True)
                continue
            if batch_res:
                for grouped_res in batch_res:
                    results_by_uuid[grouped_res.instance_uuid] = grouped_res.chunks

        # 6. [行为重映射] 逐个实例应用 "no_recall_reply" 或 "show_source"
        final_context_blocks = []
        
        for uuid in selected_uuids:
            agent_rag_config = instance_config_map.get(uuid)
            if not agent_rag_config: continue
            
            ref = ref_map[uuid]
            instance_name = ref.target_instance.name
            chunks = results_by_uuid.get(uuid, [])
            
            if chunks:
                # 场景 A: 命中数据 -> 格式化并展示 (应用 show_source)
                # Auto模式下，我们通常按知识库分块展示，以便上下文清晰
                block = self._format_chunks(chunks, agent_rag_config, source_prefix=f"[{instance_name}] ")
                if block:
                    final_context_blocks.append(block)
            else:
                # 场景 B: 未命中 -> 检查是否配置了无召回回复
                if agent_rag_config.no_recall_reply.enabled and agent_rag_config.no_recall_reply.reply_content:
                    hint = (
                        f"[System Note]: Search performed on Knowledge Base '{instance_name}' but found NO relevant information. "
                        f"If the user asks specifically about this domain, consider replying: "
                        f"'{agent_rag_config.no_recall_reply.reply_content}'"
                    )
                    final_context_blocks.append(hint)

        return "\n\n".join(final_context_blocks)

    def _group_refs_by_retrieval_params(
        self, 
        refs: List[ResourceRef], 
        config_map: Dict[str, AgentRAGConfig]
    ) -> Dict[str, Tuple[RAGConfig, List[str]]]:
        """仅基于 RAGConfig (检索参数) 进行分组，忽略行为参数。"""
        groups: Dict[str, Tuple[RAGConfig, List[str]]] = {}

        for ref in refs:
            uuid = ref.target_instance.uuid
            full_config = config_map[uuid]
            
            # [关键] 提取子集：只取检索相关参数
            retrieval_config = RAGConfig(**full_config.model_dump())
            
            # 生成指纹 (exclude_none=True 保证一致性)
            config_fingerprint = retrieval_config.model_dump_json(exclude_none=True)
            
            if config_fingerprint not in groups:
                groups[config_fingerprint] = (retrieval_config, [])
            
            groups[config_fingerprint][1].append(uuid)
            
        return groups

    async def _execute_search_batch_raw(
        self, 
        query_text: str, 
        instance_uuids: List[str], 
        rag_config: RAGConfig
    ) -> List[Any]: 
        """执行底层检索，返回原始 GroupedSearchResult 对象列表。"""
        if not instance_uuids: return []
        safe_query = query_text[:2000]

        request = KnowledgeBaseExecutionRequest(
            inputs=KnowledgeBaseExecutionParams(
                query=safe_query,
                config=rag_config
            )
        )
        
        responses = await self.execution_service.execute_batch(
            instance_uuids=instance_uuids,
            execute_params=request,
            actor=self.context.actor,
            runtime_workspace=self.runtime_workspace
        )

        # 提取 data (GroupedSearchResult)
        return [res.data for res in responses if res.success and res.data]

    async def _execute_search_batch_global(self, query_text: str, instance_uuids: List[str], config: AgentRAGConfig) -> str:
        """
        [Always Mode Implementation]
        执行全局批量搜索。特点：
        1. 混合所有知识库的结果。
        2. 全局排序（Global Sorting）。
        3. 统一处理 No Recall（如果全都没有，才提示）。
        """
        raw_results = await self._execute_search_batch_raw(
            query_text, 
            instance_uuids, 
            RAGConfig(**config.model_dump())
        )
        
        # 聚合所有 Chunks
        all_chunks: List[SearchResultChunk] = []
        for gr in raw_results:
            all_chunks.extend(gr.chunks)
            
        if not all_chunks:
            # 全局无召回处理
            if config.no_recall_reply.enabled and config.no_recall_reply.reply_content:
                return (
                    f"\n[System Note]: Retrieval performed on {len(instance_uuids)} knowledge bases but found NO relevant information. "
                    f"Suggested reply: '{config.no_recall_reply.reply_content}'"
                )
            return ""

        # 全局排序与截断
        all_chunks.sort(key=lambda x: x.score, reverse=True)
        final_chunks = all_chunks[:20] # 硬限制，防止 Context Window 爆炸
        
        return self._format_chunks(final_chunks, config)

    def _format_chunks(self, chunks: List[SearchResultChunk], config: AgentRAGConfig, source_prefix: str = "") -> str:
        """通用的 Chunk 格式化方法"""
        if not chunks: return ""
        
        context_buffer = []
        for i, chunk in enumerate(chunks):
            meta = chunk.context or {}
            source_name = meta.get('file_name', 'Unknown')
            
            # 根据配置决定是否在 Text 中暴露 Source
            source_info = f" [Source: {source_name}]" if config.show_source else ""
            
            context_buffer.append(f"--- Reference {i+1} {source_prefix}{source_info} ---\n{chunk.content}")
            
        return "\n".join(context_buffer)

    def _merge_config(self, global_config: AgentRAGConfig, ref_options: Optional[Dict]) -> AgentRAGConfig:
        if not ref_options:
            return global_config
        base_dict = global_config.model_dump()
        for k, v in ref_options.items():
            if k in base_dict and v is not None:
                base_dict[k] = v
        try:
            return AgentRAGConfig(**base_dict)
        except Exception as e:
            logger.warning(f"[RAG] Config merge failed: {e}")
            return global_config

    async def call_agent_filter(self, query_text: str, kb_refs: List[ResourceRef]) -> List[str]:
        """ Selector LLM: 筛选相关知识库 """
        if not kb_refs: return []
        
        # 1. 构造 Prompt
        candidates_info = []
        valid_uuids_set = set()
        for ref in kb_refs:
            inst = ref.target_instance
            valid_uuids_set.add(inst.uuid)
            desc = (inst.description or "")[:200]
            candidates_info.append(f"- ID: {inst.uuid}\n  Name: {inst.name}\n  Description: {desc}")
            
        system_prompt = (
            "You are a Knowledge Base Retrieval Router. "
            "Select relevant Knowledge Bases (KBs) for the user query.\n"
            "Output strict JSON: {\"selected_ids\": [\"uuid1\"]}.\n"
            "If none relevant, return {\"selected_ids\": []}."
        )
        user_prompt = f"User Query: \"{query_text}\"\n\nAvailable KBs:\n{chr(10).join(candidates_info)}\n\nJSON Selection:"

        try:
            # 2. 调用 LLM
            llm_version = await self.ai_provider.resolve_model_version(None)
            module_context = await self.ai_provider.module_service.get_runtime_context(
                version_id=llm_version.id,
                actor=self.context.actor,
                workspace=self.runtime_workspace
            )
            
            from app.services.common.llm_capability_provider import UsageAccumulator, LLMBillingCallbacks
            # 初始化用量累加器和回调
            usage_accumulator = UsageAccumulator()
            callbacks = LLMBillingCallbacks(usage_accumulator)
            result = await self.ai_provider.execute_llm_with_billing(
                runtime_workspace=self.runtime_workspace,
                module_context=module_context,
                run_config=LLMRunConfig(
                    model=module_context.version.name,
                    temperature=0.0,
                    max_tokens=500,
                    response_format={"type": "json_object"}
                ),
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt)
                ],
                callbacks=callbacks,
                usage_accumulator=usage_accumulator
            )
            
            # 3. 解析与清洗
            content = self._clean_json_markdown(result.message.content)
            data = json.loads(content)
            selected = data.get("selected_ids", [])
            
            return [uid for uid in selected if uid in valid_uuids_set]

        except Exception as e:
            logger.error(f"[RAG Selector] Failed: {e}. Fallback to ALL.", exc_info=True)
            # Fail-Safe: 如果 LLM 路由挂了，全选
            return list(valid_uuids_set)

    def _clean_json_markdown(self, text: str) -> str:
        if "```" in text:
            pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
            match = re.search(pattern, text)
            if match: return match.group(1)
        return text

class DeepMemoryProcessor(BaseContextProcessor):
    """
    负责长期记忆召回 (L1) 和 摘要注入 (L2)。
    """
    def __init__(
        self,
        context: AppContext,
        session_manager: Optional[AgentSessionManager],
        deep_memory_config: DeepMemoryConfig
    ):
        self.context = context
        self.session_manager = session_manager
        self.config = deep_memory_config
        
        # Lazy imports
        from app.services.resource.agent.memory.deep.long_term_context_service import LongTermContextService
        from app.services.resource.agent.memory.deep.context_summary_service import ContextSummaryService
        
        self.long_term_service = LongTermContextService(context)
        self.summary_service = ContextSummaryService(context)

    async def process(self, ctx: AgentPipelineContext) -> None:
        if not self.session_manager or not self.session_manager.session:
            return
        if not self.config.enabled:
            return

        query = ctx.get_query_text()
        agent_id = self.session_manager.agent_instance.id
        session_uuid = self.session_manager.session.uuid
        runtime_ws = self.session_manager.runtime_workspace
        # 这是一个快照，绝对不能包含稍后 L1 召回的 IDs
        stable_exclude_turn_ids = list(ctx.exclude_trace_ids) 
        # --- Layer 1: Vector Recall (L1) ---
        if self.config.enable_vector_recall and query.strip():
            try:
                safe_query = query[:2000]
                # L1 内部可以使用 stable_exclude_turn_ids 来避免召回已经在短期历史里的内容
                # 但 L1 召回的新 ID 绝不能回写到 stable_exclude_turn_ids 里去影响 L2
                recalled_turns = await self.long_term_service.retrieve(
                    query=safe_query,
                    agent_instance_id=agent_id,
                    session_uuid=session_uuid,
                    exclude_trace_ids=stable_exclude_turn_ids,
                    deep_memory_config=self.config,
                    runtime_workspace=runtime_ws
                )
                
                if recalled_turns:
                    blocks = []
                    for turn in turns:
                        if not turn: continue
                        trace_id = turn[0].trace_id
                        if trace_id:
                            # 注意：这里我们 NOT update ctx.exclude_trace_ids
                            turn_content = "\n".join([f"{msg.role.value}: {msg.content}" for msg in turn])
                            blocks.append(f"--- History (ID: {trace_id}) ---\n{turn_content}")
                    if blocks:
                        full_recall_text = "\n### Recalled Conversation:\n" + "\n\n".join(blocks)
                        ctx.add_context_block(full_recall_text)

            except Exception as e:
                logger.error(f"[DeepMemory] Recall failed: {e}", exc_info=True)

        # --- Layer 2: Summarization (L2) ---
        if self.config.enable_summarization:
            try:
                summaries = await self.summary_service.list_summaries(
                    agent_instance_id=agent_id,
                    user_id=self.context.actor.id,
                    session_uuid=session_uuid,
                    exclude_trace_ids=stable_exclude_turn_ids,
                    limit=self.config.max_summary_turns
                )
                
                if summaries:
                    # Summaries come in DB order (desc), reverse for chronological
                    chronological_summaries = list(reversed(summaries))
                    summary_text = self._format_summaries(chronological_summaries)
                    # 注入到 Stable Context 槽位
                    ctx.system_contexts.append(summary_text)

            except Exception as e:
                logger.error(f"[DeepMemory] Summary fetch failed: {e}", exc_info=True)

    def _format_summaries(self, summaries: List[AgentContextSummary]) -> str:
        lines = []
        for s in summaries:
            date_str = s.created_at.strftime('%Y-%m-%d') if s.created_at else "?"
            lines.append(f"- [{date_str}] [ContextID:{s.trace_id}] {s.content}")
        return "### Conversation Summaries:\n" + "\n".join(lines)
