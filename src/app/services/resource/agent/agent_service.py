# src/app/services/resource/agent/agent_service.py

import json
import logging
import uuid
import asyncio
from typing import Dict, Any, List, Callable, Optional, AsyncGenerator, Union
from decimal import Decimal
from contextlib import asynccontextmanager, nullcontext
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.core.context import AppContext
from app.core.trace_manager import TraceManager
from app.utils.async_generator import AsyncGeneratorManager
from app.models import User, Team, Workspace, Resource, ResourceInstance, ResourceRef, VersionStatus, ChatMessage, ServiceModuleVersion
from app.models.resource.agent import Agent
from app.models.interaction.chat import ChatSession, MessageRole
from app.dao.resource.agent.agent_dao import AgentDao
from app.dao.module.service_module_dao import ServiceModuleVersionDao
from app.dao.resource.resource_ref_dao import ResourceRefDao
from app.dao.product.feature_dao import FeatureDao

# Schemas
from app.schemas.resource.agent.agent_schemas import (
    AgentUpdate, AgentRead, AgentEvent, AgentExecutionRequest, AgentExecutionResponse, AgentExecutionResponseData, 
    AgentExecutionInputs, AgentConfig, GenerationDiversity, AgentRAGConfig, ModelParams, 
    InputOutputConfig, DeepMemoryConfig
)
from app.schemas.resource.knowledge.knowledge_schemas import KnowledgeBaseExecutionRequest, KnowledgeBaseExecutionParams, SearchResultChunk
from app.services.auditing.types.attributes import (
    AgentAttributes, AgentMeta, LLMMeta, LLMAttributes
)

# Services & Logic
from app.services.common.llm_capability_provider import AICapabilityProvider, UsageAccumulator
from app.services.resource.base.base_impl_service import register_service, ResourceImplementationService, ValidationResult, DependencyInfo
from app.services.resource.base.base_resource_service import BaseResourceService
from app.services.module.service_module_service import ServiceModuleService
from app.services.billing.context import BillingContext
from app.services.resource.agent.agent_session_manager import AgentSessionManager
from app.services.resource.agent.memory.agent_memory_var_service import AgentMemoryVarService
from app.services.resource.agent.prompt_template import PromptTemplate
from app.services.resource.agent.memory.deep.long_term_context_service import LongTermContextService
from app.services.resource.agent.memory.deep.context_summary_service import ContextSummaryService
from app.services.resource.agent.pipeline_manager import AgentPipelineManager
from app.services.resource.agent.processors import ResourceAwareToolExecutor
from app.services.exceptions import ServiceException, NotFoundError, ConfigurationError
from app.services.product.types.feature import FeatureRole
from app.services.resource.agent.types.agent import AgentRunResult

# Engine
from app.engine.agent import (
    AgentEngineService, AgentInput, AgentStep, AgentResult, AgentEngineCallbacks, BaseToolExecutor
)
from app.engine.model.llm import (
    LLMEngineService, LLMProviderConfig, LLMRunConfig, LLMMessage, LLMTool, LLMToolCall, LLMUsage, LLMEngineCallbacks
)
from app.engine.utils.tokenizer.manager import tokenizer_manager

logger = logging.getLogger(__name__)

class PersistingAgentCallbacks(AgentEngineCallbacks):
    """
    [Production] 负责事件推送、状态追踪和计费数据收集。
    """
    def __init__(
        self, 
        usage_accumulator: UsageAccumulator,
        generator_manager: AsyncGeneratorManager, 
        session_manager: Optional[AgentSessionManager] = None, 
        trace_id: Optional[str] = None
    ):
        self.trace_id = trace_id
        self.session_manager = session_manager
        self.generator_manager = generator_manager
        self.usage_accumulator = usage_accumulator
        self.final_result: Optional[AgentResult] = None

    async def on_agent_start(self):
        data={
            "trace_id": self.trace_id, 
            "session_uuid": self.session_manager.session.uuid if self.session_manager.session else None
        }
        await self.generator_manager.put(AgentEvent(event="start", data=data))

    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]):
        tool_calls_data = [tc.model_dump() for tc in tool_calls]
        await self.generator_manager.put(AgentEvent(event="tool_input", data={"tool_calls": tool_calls_data}))
        if self.session_manager:
            self.session_manager.buffer_message(role=MessageRole.ASSISTANT, tool_calls=tool_calls_data)
            
    async def on_agent_step(self, step: AgentStep):
        await self.generator_manager.put(AgentEvent(event="tool_output", data={
            "tool": step.action.function['name'],
            "output": step.observation
        }))
        if self.session_manager:
            self.session_manager.buffer_message(
                role=MessageRole.TOOL,
                tool_call_id=step.action.id,
                content=json.dumps(step.observation, ensure_ascii=False)
            )

    async def on_final_chunk_generated(self, chunk: str):
        # 仅用于前端流式展示
        await self.generator_manager.put(AgentEvent(event="chunk", data={"content": chunk}))

    async def on_agent_finish(self, result: AgentResult):
        self.final_result = result
        await self.generator_manager.put(AgentEvent(event="finish", data=result.model_dump()))
        # Buffer final message
        if self.session_manager and result.message.content:
            self.session_manager.buffer_message(role=MessageRole.ASSISTANT, content=result.message.content)

    async def on_agent_cancel(self, result: AgentResult) -> None:
        # [新] 现在引擎发送任何错误(不只是asyncio.CancelledError)都会发送on_agent_cancel事件并包含已生成内容和总用量，用量抢救现在由引擎负责兜底
        self.final_result = result
        # 通知前端
        await self.generator_manager.put(AgentEvent(event="cancel", data=result.model_dump()))
        # Buffer final message
        if self.session_manager and result.message.content:
            self.session_manager.buffer_message(role=MessageRole.ASSISTANT, content=result.message.content)

    async def on_agent_error(self, error: Exception):
        await self.generator_manager.put(AgentEvent(event="error", data={"error": str(error)}))

    async def on_usage(self, usage: LLMUsage):
        # [Billing Core] 计费的核心驱动力
        # 每次 LLM 调用（Prompt/Completion）都会触发此回调
        if self.usage_accumulator:
            self.usage_accumulator.add(usage)

@register_service
class AgentService(ResourceImplementationService):
    name: str = "agent"

    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = AgentDao(context.db)
        self.ref_dao = ResourceRefDao(context.db)
        self.feature_dao = FeatureDao(context.db)
        self.redis = context.redis_service
        self.module_service = ServiceModuleService(context)
        self.ai_provider = AICapabilityProvider(context)
        self.agent_memory_var_service = AgentMemoryVarService(context)
        self.long_term_service = LongTermContextService(context)
        self.prompt_template = PromptTemplate()
        self.resource_resolver = BaseResourceService(context)

    # ==========================================================================
    # Execution Logic (The Core)
    # ==========================================================================

    async def execute(
        self, 
        instance_uuid: str, 
        execute_params: AgentExecutionRequest, 
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> AgentExecutionResponse:
        """
        [Wrapper] 同步执行入口，消费 Generator 直至结束。
        """
        agent_result: Optional[AgentResult] = None
        trace_id = None
        session_uuid = None

        try:
            result = await self.async_execute(
                instance_uuid=instance_uuid, 
                execute_params=execute_params, 
                actor=actor, 
                runtime_workspace=runtime_workspace
            )
            generator = result.generator
            async for event in generator:
                if event.event == "start":
                    session_uuid = event.data.get("session_uuid")
                    trace_id = event.data.get("trace_id")
                elif event.event == "finish":
                    agent_result = event.data
                elif event.event == "cancel":
                    agent_result = event.data
                elif event.event == "error":
                    logger.error(f"Agent stream error: {event.data}")
                    # 在非流式模式下，遇到错误应抛出，以便上层捕获
                    raise ServiceException(f"Agent execution error: {event.data}")
        except Exception as e:
            logger.error(f"Critical agent execution failure: {e}", exc_info=True)
            raise ServiceException(f"Agent execution failed: {str(e)}")

        result_data = AgentExecutionResponseData(
            agent_result=agent_result,
            session_uuid=session_uuid,
            trace_id=trace_id
        )

        return AgentExecutionResponse(
            data=result_data
        )

    async def execute_batch(
        self,
        instance_uuids: List[str],
        execute_params: AgentExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> List[AgentExecutionResponse]:
        """
        Agent 暂不支持真正的并行 Batch（每个都是独立的有状态循环）。
        简单实现为循环调用。
        """
        results = []
        for uuid in instance_uuids:
            res = await self.execute(uuid, execute_params, actor, runtime_workspace)
            results.append(res)
        
        return results

    async def async_execute(
        self, 
        instance_uuid: str, 
        execute_params: AgentExecutionRequest, 
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> AgentRunResult:
        # 1. Load Instance & Workspace
        instance = await self.get_by_uuid(instance_uuid)
        if not instance: raise NotFoundError("Agent not found")
        await self._check_execute_perm(instance)

        workspace = runtime_workspace or instance.resource.workspace

        # 2. Parse Configuration
        try:
            agent_config = AgentConfig(**instance.agent_config)
        except Exception as e:
            raise ConfigurationError(f"Agent {instance.uuid} config invalid: {e}")

        # 3. Inputs
        inputs = execute_params.inputs
        if not inputs.input_query:
            raise ServiceException("Agent input query is required.")
            
        generator_manager = AsyncGeneratorManager()
        dependencies = await self.ref_dao.get_dependencies(instance.id)

        # 4. Session Manager
        trace_id = str(uuid.uuid4())
        session_manager = AgentSessionManager(
            self.context, inputs.session_uuid, trace_id, instance, workspace, actor
        )
        await session_manager.initialize()
        session = session_manager.session

        # 5. Prompt Rendering
        prompt_variables = await self.agent_memory_var_service.get_runtime_object(
            instance.version_id, actor.id, session.uuid if session else None
        )
        rendered_system_prompt = self.prompt_template.render(instance.system_prompt, prompt_variables)

        # 7. Pipeline Manager
        system_message = LLMMessage(role="system", content=rendered_system_prompt)
        history = inputs.history if not session else None
        user_message = LLMMessage(role="user", content=inputs.input_query)
        tool_executor = ResourceAwareToolExecutor(self.context, workspace)

        pipeline_manager = AgentPipelineManager(
            system_message=system_message,
            user_message=user_message,
            history=history,
            tool_executor=tool_executor
        ).add_standard_processors(
            app_context=self.context, 
            agent_config=agent_config,
            dependencies=dependencies,
            runtime_workspace=workspace,
            session_manager=session_manager
        )

        trace_manager = TraceManager(
            db=self.db,
            operation_name="agent.run",
            user_id=actor.id,
            force_trace_id=trace_id,
            target_instance_id=instance.id,
            # 运行时自动补全attributes
            attributes=None
        )

        # 10. Launch Task
        asyncio.create_task(self._run_agent_background_task(
            agent_config=agent_config,
            llm_module_version=instance.llm_module_version,
            pipeline_manager=pipeline_manager,
            runtime_workspace=workspace,
            trace_manager=trace_manager,
            generator_manager=generator_manager,
            session_manager=session_manager
        ))

        return AgentRunResult(
            generator=generator_manager,
            config=agent_config,
            trace_id=trace_id
        )

    @asynccontextmanager
    async def _session_lock(self, session_uuid: str):
        """
        [Concurrency Guard] 分布式锁，防止同一个会话并发写入。
        """
        lock_key = f"lock:session:{session_uuid}"
        # 使用 RedisService 提供的 client 获取锁
        lock = self.redis.client.lock(lock_key, timeout=60, blocking_timeout=5)
        
        acquired = await lock.acquire()
        if not acquired:
            raise ServiceException("Session is busy. Please wait for the previous response to finish.")
        try:
            yield
        finally:
            await lock.release()

    # ==========================================================================
    # Core Runtime Logic (Decoupled)
    # ==========================================================================

    async def _run_agent_background_task(
        self,
        agent_config: AgentConfig,
        llm_module_version: ServiceModuleVersion,
        pipeline_manager: AgentPipelineManager,
        runtime_workspace: Workspace,
        trace_manager: TraceManager,
        generator_manager: AsyncGeneratorManager,
        session_manager: Optional[AgentSessionManager] = None
    ):

        try:
            usage_accumulator = UsageAccumulator()

            callbacks = PersistingAgentCallbacks(
                generator_manager=generator_manager,
                session_manager=session_manager,
                trace_id=trace_manager.force_trace_id,
                usage_accumulator=usage_accumulator
            )

            # A. Pipeline Execution
            final_messages = await pipeline_manager.build_context()
            final_tools = await pipeline_manager.build_skill()

            # B. Prepare Contexts
            module_context = await self.module_service.get_runtime_context(
                version_id=llm_module_version.id,
                actor=self.context.actor,
                workspace=runtime_workspace
            )

            run_config = LLMRunConfig(
                model=module_context.version.name,
                temperature=agent_config.model_params.temperature,
                top_p=agent_config.model_params.top_p,
                presence_penalty=agent_config.model_params.presence_penalty,
                frequency_penalty=agent_config.model_params.frequency_penalty,
                # max_context_window=llm_attributes.get("context_window", 4096),
                max_tokens=agent_config.io_config.max_response_tokens,
                enable_thinking=agent_config.io_config.enable_deep_thinking,
                thinking_budget=agent_config.io_config.max_thinking_tokens,
                tools=final_tools, 
                stream=True
            )

            # C. Session Locking & Trace Context
            session = session_manager.session if session_manager and session_manager.session else None
            lock_ctx = self._session_lock(session.uuid) if session else nullcontext()
            if session:
                # Buffer user input
                user_message = pipeline_manager.user_message
                session_manager.buffer_message(role=MessageRole.USER, content=user_message.content)

            async with lock_ctx:
                # [TRACE SCOPE START]
                async with trace_manager as root_span:
                    try:
                        agent_input=AgentInput(messages=final_messages)
                        root_span.attributes = AgentAttributes(
                            # 不再需要记录用量，因为result包含了
                            meta=AgentMeta(config=run_config),
                            inputs=agent_input
                        )
                        result = await self.ai_provider.execute_agent_with_billing(
                            runtime_workspace=runtime_workspace,
                            module_context=module_context,
                            agent_input=agent_input,
                            run_config=run_config,
                            tool_executor=pipeline_manager.tool_executor,
                            callbacks=callbacks,
                            usage_accumulator=usage_accumulator
                        )
                        # Happy Path: Update Trace
                        root_span.set_output(result)
                    except Exception as e:
                        # Error Path: Try to recover partial result for Trace
                        if callbacks.final_result:
                            # 如果是 on_agent_cancel 产生的 result，包含部分内容
                            root_span.set_output(callbacks.final_result)
                        raise # Re-raise to trigger outer catch block
                    finally:
                        # Session Commit (Inside Trace Scope for correct timing, but protected)
                        if session:
                            try:
                                await session_manager.commit(agent_config.deep_memory)
                            except Exception as e:
                                logger.error(f"Failed to commit session: {e}", exc_info=True)
                # [TRACE SCOPE END] - Span is written to DB here

        except asyncio.CancelledError:
            logger.info(f"Agent task cancelled. TraceID: {trace_manager.force_trace_id}")
            raise 
        except Exception as e:
            logger.error(f"Agent task error: {e}", exc_info=True)
            await generator_manager.put(AgentEvent(event="error", data={"error": str(e)}))
        finally:
            await generator_manager.aclose(force=False)

    # --- CRUD Implementation ---

    async def get_by_uuid(self, instance_uuid: str) -> Optional[Agent]:
        return await self.dao.get_by_uuid(instance_uuid)

    async def create_instance(self, resource: Resource, actor: User) -> Agent:
        # 1. 获取默认 LLM 模型
        default_llm = await self.module_service.smv_dao.get_default_version_by_type("llm")
        if not default_llm:
            raise ConfigurationError("System configuration error: No default LLM module found.")

        # 2. 构建健壮的默认配置
        default_config = AgentConfig(
            model_params=ModelParams(),
            io_config=InputOutputConfig(),
            rag_config=AgentRAGConfig(enabled=False),
            deep_memory=DeepMemoryConfig(enabled=False, summary_model_uuid=default_llm.uuid)
        )

        # limit_feature = await self.feature_dao.get_by_name("limit:agent:custom:execution")
        limit_feature = None
        
        instance = Agent(
            version_tag="__workspace__",
            status=VersionStatus.WORKSPACE,
            creator_id=actor.id,
            resource_type="agent",
            name=resource.name,
            resource=resource,
            agent_config=default_config.model_dump(mode='json'),
            system_prompt="You are a helpful AI assistant.",
            llm_module_version_id=default_llm.id,
            linked_feature_id=limit_feature.id if limit_feature else None
        )
        return instance

    async def update_instance(self, instance: ResourceInstance, update_data: Dict[str, Any]) -> Agent:
        if not isinstance(instance, Agent): raise ServiceException("Not an Agent")
        
        try:
            validated = AgentUpdate.model_validate(update_data)
        except Exception as e:
            raise ServiceException(f"Invalid update data: {e}")

        data = validated.model_dump(exclude_unset=True)
        
        if "llm_module_version_uuid" in data:
            uuid_val = data.pop("llm_module_version_uuid")
            if uuid_val:
                smv = await self.module_service.smv_dao.get_one(where={"uuid": uuid_val})
                if not smv:
                    raise NotFoundError(f"LLM Module Version {uuid_val} not found.")
                # 这里可以加更多检查，比如类型是否为 LLM
                instance.llm_module_version_id = smv.id
            # Else ignore or handle unbind logic

        if "agent_config" in data:
            current_config = AgentConfig(**instance.agent_config)
            updated_config = current_config.model_copy(update=data["agent_config"])
            instance.agent_config = updated_config.model_dump(mode='json')
            data.pop("agent_config")

        for k, v in data.items():
            if hasattr(instance, k):
                setattr(instance, k, v)
        
        return instance

    async def delete_instance(self, instance: Agent) -> None:
        await self.db.delete(instance)

    async def on_resource_delete(self, resource: Resource) -> None:
        pass

    async def publish_instance(self, workspace_instance: Agent, version_tag: str, version_notes: Optional[str], actor: User) -> Agent:
        snapshot = Agent(
            resource_id=workspace_instance.resource_id,
            status=VersionStatus.PUBLISHED,
            version_tag=version_tag,
            version_notes=version_notes,
            creator_id=actor.id,
            published_at=func.now(),
            name=workspace_instance.name,
            description=workspace_instance.description,
            system_prompt=workspace_instance.system_prompt,
            agent_config=workspace_instance.agent_config,
            llm_module_version_id=workspace_instance.llm_module_version_id
        )
        return snapshot

    async def validate_instance(self, instance: Agent) -> ValidationResult:
        errors = []
        if not instance.llm_module_version_id:
            errors.append("Must select an LLM model.")
        try:
            AgentConfig(**instance.agent_config)
        except Exception as e:
            errors.append(f"Invalid configuration: {str(e)}")
        return ValidationResult(is_valid=not errors, errors=errors)

    async def get_dependencies(self, instance: Agent) -> List[DependencyInfo]:
        refs = await self.ref_dao.get_dependencies(instance.id)
        return [
            DependencyInfo(
                resource_uuid=ref.target_resource.uuid,
                instance_uuid=ref.target_instance.uuid,
                alias=ref.alias
            ) for ref in refs
        ]

    async def get_searchable_content(self, instance: Agent) -> str:
        return f"{instance.name} {instance.description or ''} {instance.system_prompt or ''}"

    async def serialize_instance(self, instance: Agent) -> Dict[str, Any]:
        data = AgentRead.model_validate(instance).model_dump()
        data["llm_module_version_uuid"] = instance.llm_module_version.uuid
        return data

    async def as_llm_tool(self, instance: Agent) -> Optional[LLMTool]:
        return LLMTool(
            type="function",
            function={
                "name": f"call_agent_{instance.uuid.replace('-', '_')}",
                "description": instance.description or f"Ask agent {instance.name}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The question to ask the agent."}
                    },
                    "required": ["query"]
                }
            }
        )
