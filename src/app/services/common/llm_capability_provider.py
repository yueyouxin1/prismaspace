# src/app/services/common/llm_capability_provider.py

import logging
import asyncio
from typing import List, Optional, Callable, TypeVar, Any, Union, Dict
from decimal import Decimal
from dataclasses import dataclass, field

from app.core.context import AppContext
from app.models import User, Workspace, ServiceModuleVersion, ServiceModuleStatus
from app.dao.module.service_module_dao import ServiceModuleVersionDao
from app.services.module.types.service_module import ModuleRuntimeContext
from app.services.billing.context import BillingContext
from app.services.product.types.feature import FeatureRole
from app.services.exceptions import ConfigurationError, NotFoundError, ServiceException

# Engine Imports
from app.engine.model.llm import (
    LLMEngineService, 
    LLMProviderConfig, 
    LLMRunConfig, 
    LLMMessage, 
    LLMResult, 
    LLMEngineCallbacks, 
    LLMUsage,
    LLMToolCall
)
from app.engine.agent import (
    AgentEngineService, 
    AgentInput, 
    AgentResult, 
    AgentEngineCallbacks, 
    BaseToolExecutor,
    AgentStep
)

logger = logging.getLogger(__name__)

T_Result = TypeVar("T_Result")

@dataclass
class UsageAccumulator:
    """
    [Side Channel] 可变的用量累加器。
    用于在 Billing Wrapper 和 Engine 回调之间传递实时用量数据。
    即使 Engine 抛出异常，外层持有的 Accumulator 实例依然保留已消耗的用量。
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, usage: LLMUsage):
        if not usage: return
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens

class LLMBillingCallbacks(LLMEngineCallbacks):
    """
    [Internal] 捕获生成的摘要内容，并向累加器汇报用量。
    """
    def __init__(self, usage_accumulator: UsageAccumulator):
        self.usage_accumulator = usage_accumulator
        self.final_result: Optional[LLMResult] = None

    async def on_success(self, result: LLMMessage) -> None:
        self.final_result = result

    async def on_cancel(self, result: LLMMessage) -> None:
        self.final_result = result

    async def on_usage(self, usage: LLMUsage):
        # [Critical] 将 Engine 产生的用量同步到外部的累加器中
        # 这样 Provider 的计费 Wrapper 才能在 finally 块中获取到真实数据
        if self.usage_accumulator:
            self.usage_accumulator.add(usage)

    async def on_error(self, error: Exception) -> None:
        logger.error(f"Context summary generation failed in engine: {error}")
    
    # 必须实现协议中的其他方法
    async def on_start(self): pass
    async def on_chunk_generated(self, chunk: str): pass
    async def on_tool_calls_generated(self, tool_calls): pass

class AICapabilityProvider:
    """
    [Platform Infrastructure] AI 能力提供者。
    提供统一的模型解析、计费包裹和引擎调用。
    """

    def __init__(self, context: AppContext):
        self.context = context
        self.smv_dao = ServiceModuleVersionDao(context.db)
        # 引擎层是无状态的，可以复用
        self._llm_engine = LLMEngineService()
        
    # ==========================================================================
    # 1. Resource Resolution
    # ==========================================================================

    async def resolve_model_version(self, module_uuid: Optional[str] = None) -> ServiceModuleVersion:
        """解析目标模型版本，支持回退到系统默认。"""
        if module_uuid:
            version = await self.smv_dao.get_by_uuid(module_uuid)
            if version and version.status == ServiceModuleStatus.AVAILABLE:
                return version
            logger.warning(f"Configured model {module_uuid} not available. Falling back to default.")

        version = await self.smv_dao.get_default_version_by_type("llm")
        if not version:
            raise ConfigurationError("System Error: No default LLM module configured.")
        
        return version

    # ==========================================================================
    # 2. Billing Aspect (EFS Protocol with Side Channel Tracking)
    # ==========================================================================

    async def with_billing(
        self,
        runtime_workspace: Workspace,
        module_context: ModuleRuntimeContext,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        usage_accumulator: UsageAccumulator,
        execution_func: Callable[[], T_Result]
    ) -> T_Result:
        """
        [EFS Billing Wrapper]
        
        执行逻辑：
        1. 冻结预算。
        2. 执行 execution_func (调用方需确保该 Func 会更新 usage_accumulator)。
        3. 无论成功失败，读取 usage_accumulator 进行结算。
        """
        billing_entity = runtime_workspace.billing_owner
        is_custom_credential = module_context.credential.is_custom

        # 1. BYOK 场景：只执行，不计费，但依然追踪用量（为了审计）
        if is_custom_credential:
            return await execution_func()

        # 2. 解析 Feature
        input_feature = next((f for f in module_context.features if f.feature_role == FeatureRole.LLM_INPUT), None)
        output_feature = next((f for f in module_context.features if f.feature_role == FeatureRole.LLM_OUTPUT), None)

        if not input_feature or not output_feature:
            raise ConfigurationError(f"Billing features missing for module '{module_context.version.name}'.")

        # 3. 执行 EFS 流程
        async with BillingContext(self.context, billing_entity) as bc:
            receipt_in = None
            receipt_out = None
            
            # --- Estimate & Freeze ---
            if estimated_input_tokens > 0:
                receipt_in = await bc.reserve(input_feature, Decimal(estimated_input_tokens))
            
            if estimated_output_tokens > 0:
                receipt_out = await bc.reserve(output_feature, Decimal(estimated_output_tokens))

            try:
                # --- Execute ---
                result = await execution_func()
                if usage_accumulator.total_tokens == 0 and hasattr(result, "usage"):
                    usage_accumulator.add(result.usage)
                return result
            finally:
                # --- Settle (Regardless of Success/Failure) ---
                # 即使上一步抛出异常，usage_accumulator 也有值
                if receipt_in:
                    actual_input = Decimal(usage_accumulator.prompt_tokens)
                    await bc.report_usage(receipt_in, input_feature, actual_input)
                
                if receipt_out:
                    actual_output = Decimal(usage_accumulator.completion_tokens)
                    await bc.report_usage(receipt_out, output_feature, actual_output)

    # ==========================================================================
    # 3. Atomic Capability: LLM
    # ==========================================================================

    async def execute_llm(
        self,
        module_context: ModuleRuntimeContext,
        run_config: LLMRunConfig,
        messages: List[LLMMessage],
        callbacks: Optional[LLMEngineCallbacks] = None,
        # 内部使用，用于连接 Billing Wrapper
        _usage_accumulator: Optional[UsageAccumulator] = None
    ) -> LLMResult:
        
        provider_config = LLMProviderConfig(
            client_name=module_context.version.attributes.get('client_name'),
            api_key=module_context.credential.api_key,
            base_url=module_context.credential.endpoint
        )

        return await self._llm_engine.run(
            provider_config=provider_config,
            run_config=run_config,
            messages=messages,
            callbacks=callbacks
        )

    async def execute_llm_with_billing(
        self,
        runtime_workspace: Workspace,
        module_context: ModuleRuntimeContext,
        run_config: LLMRunConfig,
        messages: List[LLMMessage],
        callbacks: LLMEngineCallbacks,
        usage_accumulator: UsageAccumulator
    ) -> LLMResult:
        """
        [Composite] 执行 LLM 并计费。
        """
        # 简单预估
        estimated_input = len(str(messages)) // 3
        estimated_output = run_config.max_tokens

        # 定义一个闭包，接收 accumulator
        async def _task():
            return await self.execute_llm(
                module_context, run_config, messages, callbacks
            )

        return await self.with_billing(
            runtime_workspace=runtime_workspace,
            module_context=module_context,
            estimated_input_tokens=estimated_input,
            estimated_output_tokens=estimated_output,
            execution_func=_task
        )

    # ==========================================================================
    # 4. Atomic Capability: Agent
    # ==========================================================================

    async def execute_agent(
        self,
        module_context: ModuleRuntimeContext,
        agent_input: AgentInput,
        run_config: LLMRunConfig,
        tool_executor: BaseToolExecutor,
        callbacks: Optional[AgentEngineCallbacks] = None,
        max_iterations: int = 10
    ) -> AgentResult:
        
        provider_config = LLMProviderConfig(
            client_name=module_context.version.attributes.get('client_name'),
            api_key=module_context.credential.api_key,
            base_url=module_context.credential.endpoint
        )

        agent_engine = AgentEngineService(
            llm_engine=self._llm_engine,
            max_iterations=max_iterations
        )

        return await agent_engine.run(
            agent_input=agent_input,
            provider_config=provider_config,
            run_config=run_config,
            tool_executor=tool_executor,
            callbacks=callbacks
        )

    async def execute_agent_with_billing(
        self,
        runtime_workspace: Workspace,
        module_context: ModuleRuntimeContext,
        agent_input: AgentInput,
        run_config: LLMRunConfig,
        tool_executor: BaseToolExecutor,
        callbacks: AgentEngineCallbacks,
        usage_accumulator: UsageAccumulator,
        max_iterations: int = 10
    ) -> AgentResult:
        """
        [Composite] 执行 Agent 并计费。
        """
        estimated_input = len(str(agent_input.messages)) // 3
        # 宽松预估：Agent 会多次调用 LLM，所以预估量适当放大
        estimated_output = run_config.max_tokens * 2 

        async def _task():
            return await self.execute_agent(
                module_context, agent_input, run_config, tool_executor, callbacks, max_iterations
            )

        return await self.with_billing(
            runtime_workspace=runtime_workspace,
            module_context=module_context,
            estimated_input_tokens=estimated_input,
            estimated_output_tokens=estimated_output,
            usage_accumulator=usage_accumulator,
            execution_func=_task
        )