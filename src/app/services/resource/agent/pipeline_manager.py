# src/app/services/resource/agent/pipeline_manager.py

from typing import List, Optional
from app.engine.model.llm import LLMMessage, LLMTool
from app.engine.agent import BaseToolExecutor
from app.core.context import AppContext
from app.models import Workspace, ResourceRef
from app.schemas.resource.agent.agent_schemas import AgentConfig
from app.services.resource.agent.agent_session_manager import AgentSessionManager

# Processors
from .processors import (
    AgentPipelineContext, BaseContextProcessor, BaseSkillProcessor,
    ShortContextProcessor, RAGContextProcessor, DeepMemoryProcessor,
    DependencySkillsProcessor, DeepMemorySkillsProcessor, MemoryVarSkillsProcessor
)

class AgentPipelineManager:
    """
    [Manager] 负责上下文&能力处理流水线的生命周期管理。
    """
    def __init__(
        self,
        user_message: LLMMessage,
        tool_executor: BaseToolExecutor,
        system_message: Optional[LLMMessage] = None,
        history: Optional[List[LLMMessage]] = None
    ):
        self.system_message = system_message
        self.history = history
        self.user_message = user_message
        self.tool_executor = tool_executor
        
        # 分离 Context 和 Skill 的处理器列表
        self._context_processors: List[BaseContextProcessor] = []
        self._skill_processors: List[BaseSkillProcessor] = []

    def add_standard_processors(  
        self, 
        app_context: AppContext,
        agent_config: AgentConfig,
        dependencies: List[ResourceRef],
        runtime_workspace: Workspace,
        session_manager: Optional[AgentSessionManager],
    ):
        """
        [Standard Pack] 一键装配标准 Agent 所需的所有上下文策略和能力。
        包含了：
        - Context: 短期记忆, RAG, 长期记忆召回
        - Skills: 外部工具依赖, 长期记忆扩展工具, 记忆变量读写工具
        """
        
        # ======================================================================
        # 1. Context Processors (构建 Prompt 上下文)
        # ======================================================================
        
        # 1.1 Short Term History
        if session_manager:
            self._context_processors.append(ShortContextProcessor(
                app_context, 
                session_manager, 
                agent_config.io_config.history_turns
            ))
        
        # 1.2 RAG (VectorDB)
        self._context_processors.append(RAGContextProcessor(
            app_context, 
            dependencies, 
            agent_config.rag_config, 
            runtime_workspace
        ))
        
        # 1.3 Deep Memory Context (L1 Recall & L2 Summary)
        if session_manager and agent_config.deep_memory.enabled:
            self._context_processors.append(DeepMemoryProcessor(
                app_context, 
                session_manager, 
                agent_config.deep_memory
            ))

        # ======================================================================
        # 2. Skill Processors (构建 Tool 列表)
        # ======================================================================

        # 2.1 External Dependencies (Resource Tools)
        self._skill_processors.append(DependencySkillsProcessor(
            app_context, 
            dependencies
        ))

        # 2.2 Deep Memory Tools (expand_long_term_context)
        if agent_config.deep_memory.enabled:
            self._skill_processors.append(DeepMemorySkillsProcessor(
                app_context, 
                agent_config.deep_memory
            ))

        # 2.3 Memory Variable Tools (memory_get, memory_set)
        if session_manager:
            self._skill_processors.append(MemoryVarSkillsProcessor(
                app_context, 
                session_manager
            ))
        
        return self

    def add_context_processor(self, processor: BaseContextProcessor):
        """允许添加自定义 Context Processor"""
        self._context_processors.append(processor)
        return self

    def add_skill_processor(self, processor: BaseSkillProcessor):
        """允许添加自定义 Skill Processor"""
        self._skill_processors.append(processor)
        return self

    async def build_context(
        self,
        ) -> List[LLMMessage]:
        """
        执行流水线，返回最终的 Messages 列表。
        """
        if not self._context_processors:
            return []
        # 初始化 Pipeline Context
        pipeline_ctx = AgentPipelineContext(
            system_message=self.system_message,
            history=self.history,
            user_message=self.user_message
        )

        # 顺序执行 Processors
        for processor in self._context_processors:
            await processor.process(pipeline_ctx)
            
        return pipeline_ctx.to_llm_messages()

    async def build_skill(
        self,
        ) -> List[LLMTool]:
        """
        执行流水线，返回最终的 Tool 列表。
        """
        if not self._skill_processors:
            return []
        # 顺序执行 Processors
        for processor in self._skill_processors:
            await processor.process(self.tool_executor)
            
        return self.tool_executor.get_llm_tools()