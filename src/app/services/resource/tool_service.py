# app/services/resource/tool_service.py

from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import attributes, joinedload, selectinload, with_polymorphic
from typing import Optional, Dict, Any, List
from pydantic import ValidationError
from app.core.config import settings
from app.core.context import AppContext
from app.dao.resource.tool.tool_dao import ToolDao
from app.dao.product.feature_dao import FeatureDao
from app.models import User, Team, Workspace, Project, Trace
from app.models.resource import Resource, ResourceInstance, VersionStatus
from app.models.resource.tool import Tool
from app.schemas.resource.tool_schemas import ToolUpdate, ToolRead, ToolExecutionRequest, ToolExecutionResponse
from app.services.billing.context import BillingContext
from app.services.billing.types.interceptor import ReservationReceipt
from app.services.billing.interceptor import InsufficientFundsError
from app.services.exceptions import ServiceException
from .base.base_impl_service import register_service, ResourceImplementationService, ValidationResult, DependencyInfo
from app.engine.schemas.parameter_schema import ParameterSchema, SchemaBlueprint, ParameterValue
from app.engine.tool.callbacks import ToolEngineCallbacks
from app.engine.tool.main import ToolEngineService
from app.engine.model.llm import LLMTool, LLMToolFunction
from app.engine.utils.parameter_schema_utils import build_json_schema_node
from app.core.trace_manager import TraceManager
from app.services.auditing.types.attributes import ToolAttributes, ToolMeta

class _ToolExecutionCallbacks(ToolEngineCallbacks):
    def __init__(self, db_session: AsyncSession, actor: User, execution_context: Dict[str, Any]):
        self.db = db_session
        self.actor = actor
        self.execution_context = execution_context # 存储上下文
        self.root_trace: Optional[Trace] = None

    async def on_start(self, execution_context: Dict[str, Any], inputs: Dict[str, Any]) -> None:
        print("\n" + "="*50)
        print(f"🚀 [START] Execution initiated. Context: {execution_context}")
        print(f"📥 [INPUTS] Runtime arguments received: {inputs}")
        print("─"*50)

    async def on_log(self, message: str, metadata: Dict[str, Any] = None) -> None:
        print(f"📝 [LOG] {message}")
        if metadata:
            import json
            # Pretty-print metadata for readability
            print(f"  └─ Metadata: {json.dumps(metadata, indent=2)}")

    async def on_success(self, result: Dict[str, Any], raw_response: Any) -> None:
        print("─"*50)
        print("✅ [SUCCESS] Execution completed successfully.")
        import json
        print(f"✨ [SHAPED RESULT] The final, structured output is:\n{json.dumps(result, indent=2)}")
        print("="*50 + "\n")

    async def on_error(self, error: Exception) -> None:
        print("─"*50)
        print(f"❌ [ERROR] An error occurred during execution: {type(error).__name__}")
        print(f"  └─ Details: {error}")
        print("="*50 + "\n")

@register_service
class ToolService(ResourceImplementationService):
    name: str = "tool"
        
    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = ToolDao(context.db)
        self.feature_dao = FeatureDao(context.db)
        self.update_schema = ToolUpdate
        self.read_schema = ToolRead
        self.polymorphic_instance = with_polymorphic(ResourceInstance, [Tool])
        self.engine = ToolEngineService()

    # --- 必要通用接口实现 ---

    async def serialize_instance(self, instance: Tool) -> Dict[str, Any]:
        """[新] 序列化职责下沉。"""
        return self.read_schema.model_validate(instance, from_attributes=True).model_dump(exclude_unset=True)

    async def get_by_uuid(self, instance_uuid: str) -> Tool | None:
        return await self.dao.get_by_uuid(instance_uuid)

    async def create_instance(self, resource: Resource, actor: User) -> Tool:
        """创建Tool实例对象（内存操作）。"""
        feature = await self.feature_dao.get_by_name("limit:tool:custom:execution")
        # [健壮性修复] 检查 Feature 是否存在
        if not feature:
            raise ConfigurationError(
                "Default feature 'limit:tool:custom:execution' not found. "
                "Please ensure the database is seeded correctly."
            )
        instance_details = {
            "version_tag": "__workspace__",
            "status": VersionStatus.WORKSPACE,
            "creator_id": actor.id,
            "resource_type": self.name,
            "name": resource.name,
            "description": resource.description,
            "linked_feature_id": feature.id
        }
        tool_defaults = {
            "url": None,  # 使用空字符串作为默认URL
            "method": "GET",
            "inputs_schema": [],
            "outputs_schema": []
        }
        return Tool(**instance_details, **tool_defaults, resource=resource)

    async def publish_instance(
        self, 
        workspace_instance: Tool, 
        version_tag: str, 
        version_notes: Optional[str], 
        actor: User
    ) -> Tool:
        """
        [专家实现] 为一个 Tool 实例创建一个发布版本。
        """
        # 使用 SQLAlchemy 的 get_history 来安全地获取当前状态
        source_state = attributes.instance_state(workspace_instance)
        
        # 复制所有持久化（非关系）的属性
        snapshot_data = {
            col.key: getattr(workspace_instance, col.key)
            for col in source_state.mapper.columns # <-- THE FIX IS HERE
            # We exclude primary keys and UUIDs which should be newly generated or are irrelevant.
            if col.key not in ['id', 'uuid', 'version_id', 'created_at'] 
        }
        
        # 覆盖关键的快照元数据
        snapshot_data.update({
            "resource_id": workspace_instance.resource_id,
            "status": VersionStatus.PUBLISHED,
            "version_tag": version_tag,
            "version_notes": version_notes,
            "creator_id": actor.id,
            "published_at": func.now() # 使用数据库函数
        })
        
        return Tool(**snapshot_data)

    async def update_instance(self, instance: ResourceInstance, update_data: Dict[str, Any]) -> Tool:
        """
        [核心职责] 完整的更新流程：验证 -> 更新 -> 返回。
        """
        if not isinstance(instance, Tool):
            raise ServiceException("Instance is not a Tool.")
        if instance.status != VersionStatus.WORKSPACE:
            raise ServiceException("Only workspace instances can be edited.")
        # 1. [新] 验证职责下沉
        try:
            validated_data = self.update_schema.model_validate(update_data)
        except ValidationError as e:
            raise e # 将 Pydantic 错误向上抛出

        # 2. 更新对象
        update_dict = validated_data.model_dump(exclude_unset=True)
        
        for key, value in update_dict.items():
            setattr(instance, key, value)
            
        return instance

    async def delete_instance(self, instance: Tool) -> None:
        """
        Handles the specific cleanup and deletion logic for a Tool instance.
        """
        # [示例] 如果删除Tool需要特定逻辑，比如通知外部系统，就在这里做
        # print(f"De-registering tool {instance.uuid} from external service...")
        
        # 然后执行通用的删除
        await self.db.delete(instance)

    async def on_resource_delete(self, resource: Resource) -> None:
        """
        Tool 没有关联的物理基础设施，无需清理。
        """
        pass
        
    async def validate_instance(self, instance: Tool) -> ValidationResult:
        """校验一个Tool实例是否准备就绪。"""
        errors = []
        if not instance.url:
            errors.append("API端点URL不能为空。")
        
        # 简单的 schema 检查
        if not instance.inputs_schema and not instance.outputs_schema:
            errors.append("必须至少定义输入或输出参数之一。")
            
        # [未来] 可以在这里添加更复杂的检查，比如尝试连接URL或验证Schema的深度
        
        return ValidationResult(is_valid=not errors, errors=errors)

    async def get_dependencies(self, instance: Tool) -> List[DependencyInfo]:
        """
        一个Tool通常是依赖图的叶节点，没有下游资源依赖。
        因此，它返回一个空列表。
        """
        return []

    async def get_searchable_content(self, instance: Tool) -> str:
        """
        [未来] 将Tool的关键信息拼接成一个字符串，用于搜索。
        """
        pass

    async def as_llm_tool(self, instance: Tool) -> Optional[LLMTool]:
        """
        将 Tool 资源转换为 LLM Tool 定义。
        """
        # 1. 优先使用显式定义的 llm_function_schema
        if instance.llm_function_schema:
             # 确保结构符合 OpenAI 标准
             return LLMTool(**instance.llm_function_schema)
        
        # 2. 如果没有，尝试从 inputs_schema 自动转换
        properties = {}
        required = []
        if instance.inputs_schema:
            for param in instance.inputs_schema:
                if param.name:
                    properties[param.name] = build_json_schema_node(param)
                    if param.required:
                        required.append(param.name)
        
        parameters = {
            "type": "object",
            "properties": properties,
            "required": required
        }
        
        return LLMTool(
            type="function",
            function=LLMToolFunction(
                name=f"call_{instance.uuid.replace('-', '_')}", # 使用 UUID 保证唯一性
                description=instance.description or instance.name,
                parameters=parameters
            )
        )

    # --- 领域接口实现 ---

    async def execute(
        self, 
        instance_uuid: str,
        execute_params: ToolExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> ToolExecutionResponse:
        # 领域服务自治：自己加载完整的 ORM 对象
        instance = await self.get_by_uuid(instance_uuid)
        await self._check_execute_perm(instance)
        workspace = runtime_workspace or instance.resource.workspace
        billing_entity = workspace.billing_owner

        # 1. 准备 Trace Attributes
        trace_attrs = ToolAttributes(
            inputs=execute_params.inputs,
            meta=ToolMeta(
                tool_name=instance.name,
                http_method=instance.method,
                url=str(instance.url)
            )
        )

        async with TraceManager(
            db=self.db,
            operation_name="tool.execute",
            user_id=actor.id,
            target_instance_id=instance.id,
            attributes=trace_attrs
        ) as span:

            async with BillingContext(self.context, billing_entity) as bc:

                receipt: Optional[ReservationReceipt] = None
                
                # 直接从 instance 获取计费特性
                if instance.linked_feature:
                    reserve_usage = Decimal('1')
                    receipt = await bc.reserve(
                        feature=instance.linked_feature,
                        reserve_usage=reserve_usage
                    )
                
                # --- 执行核心业务 ---
                runtime_arguments = execute_params.inputs
                meta_options = execute_params.meta
                return_raw_response = meta_options.return_raw_response if meta_options else False
                if instance.status != VersionStatus.WORKSPACE:
                    return_raw_response = False
        
                execution_context = {"instance_uuid": instance.uuid, "name": instance.name}
                callbacks = _ToolExecutionCallbacks(self.db, actor, execution_context)

                parsed_inputs_schema = [ParameterSchema.model_validate(p) for p in instance.inputs_schema]
                parsed_outputs_schema = [ParameterSchema.model_validate(p) for p in instance.outputs_schema]

                try:
                    result_dict = await self.engine.run(
                        method=instance.method,
                        url=instance.url,
                        inputs_schema=parsed_inputs_schema,
                        outputs_schema=parsed_outputs_schema,
                        runtime_arguments=runtime_arguments,
                        callbacks=callbacks,
                        execution_context=execution_context,
                        return_raw_response=return_raw_response
                    )
                    span.set_output(result_dict)
                except Exception as e:
                    raise ServiceException(str(e))

                # --- 3. 凭收据报告真实用量 ---
                if receipt and instance.linked_feature:
                    actual_usage = Decimal('1')
                    await bc.report_usage(
                        receipt=receipt,
                        feature=instance.linked_feature,
                        actual_usage=actual_usage
                    )

                return ToolExecutionResponse(data=result_dict)

    async def execute_batch(
        self,
        instance_uuids: List[str],
        execute_params: ToolExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> List[ToolExecutionResponse]:
        pass
