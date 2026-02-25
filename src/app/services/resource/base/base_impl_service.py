# src/app/services/resource/base/base_impl_service.py

from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Type, Dict, Any, Optional, List, NamedTuple
from .common import CommonResourceService
from app.core.context import AppContext
from app.models import User, Team, Workspace
from app.models.resource import Resource, ResourceInstance
from app.services.billing.context import BillingContext
from app.schemas.resource.execution_schemas import AnyExecutionRequest, AnyExecutionResponse
from app.engine.model.llm import LLMTool

# [新增] 定义一个标准的数据结构来表示校验结果
class ValidationResult(NamedTuple):
    is_valid: bool
    errors: List[str]

# [新增] 定义一个标准的数据结构来表示依赖关系
class DependencyInfo(NamedTuple):
    resource_uuid: str
    instance_uuid: str
    alias: Optional[str]

class ResourceImplementationService(CommonResourceService, ABC):
    """
    [接口] 定义了所有具体资源类型服务必须实现的统一契约。
    """
    name: str = "resource_imple"

    def __init__(self, context: AppContext):
        super().__init__(context)
        
    # --- 1. CRUD & Lifecycle (from v1.0) ---

    @abstractmethod
    async def get_by_uuid(self, instance_uuid: str) -> Optional[ResourceInstance]:
        raise NotImplementedError

    @abstractmethod
    async def create_instance(self, resource: Resource, actor: User) -> ResourceInstance:
        raise NotImplementedError

    @abstractmethod
    async def update_instance(self, instance: ResourceInstance, update_data: Dict[str, Any]) -> ResourceInstance:
        raise NotImplementedError

    @abstractmethod
    async def delete_instance(self, instance: ResourceInstance) -> None:
        raise NotImplementedError

    @abstractmethod
    async def on_resource_delete(self, resource: Resource) -> None:
        """
        当整个资源被删除时触发。
        职责：清理与该资源绑定的所有物理设施（如 Drop Schema, Drop Collection）。
        注意：不需要在此处删除 ResourceInstance 的数据库记录，外层会通过 Cascade 处理。
        """
        raise NotImplementedError

    @abstractmethod
    async def publish_instance(
        self, 
        workspace_instance: ResourceInstance, 
        version_tag: str, 
        version_notes: Optional[str], 
        actor: User
    ) -> ResourceInstance:
        raise NotImplementedError

    # --- 2. Validation & Pre-flight Checks (NEW) ---

    @abstractmethod
    async def validate_instance(self, instance: ResourceInstance) -> ValidationResult:
        """
        [必要] 校验一个资源实例是否完整且有效，可以被发布或执行。
        
        - **目的**: 在执行关键操作（如发布）前，进行“飞行前检查”。
        - **调用时机**: 由 ResourceService 在 `_publish_instance` 流程的开始阶段调用。
        - **返回**: 一个 ValidationResult 对象，包含一个布尔值和一组人类可读的错误信息。
        """
        raise NotImplementedError

    # --- 3. Dependency Resolution (NEW) ---

    @abstractmethod
    async def get_dependencies(self, instance: ResourceInstance) -> List[DependencyInfo]:
        """
        [必要] 解析并返回一个资源实例的所有直接依赖项。
        
        - **目的**: 用于UI展示依赖图、进行发布前的依赖检查。
        - **调用时机**: 可由前端API调用以渲染UI，也可在 `validate_instance` 内部被调用。
        - **返回**: 一个标准化的依赖信息列表。
        """
        raise NotImplementedError

    # --- 4. Discovery & Indexing (NEW) ---

    @abstractmethod
    async def get_searchable_content(self, instance: ResourceInstance) -> str:
        """
        [必要] 提取一个资源实例中所有可供全文搜索的文本内容。
        
        - **目的**: 为搜索引擎（如Elasticsearch）提供标准化的索引源。
        - **调用时机**: 由后台的索引Worker在资源创建或更新后异步调用。
        - **返回**: 一个拼接了所有关键信息的长字符串。
        """
        raise NotImplementedError

    @abstractmethod
    async def as_llm_tool(self, instance: ResourceInstance) -> Optional[LLMTool]:
        """
        [关键新增] 将资源实例转换为 OpenAI Function Definition 格式。
        允许 Agent 引擎理解并调用此资源。
        """
        raise NotImplementedError
        
    # --- 5. Presentation (from v1.0) ---
    
    @abstractmethod
    async def serialize_instance(self, instance: ResourceInstance) -> Dict[str, Any]:
        raise NotImplementedError

    # --- 6. Execute ---

    @abstractmethod
    async def execute(
        self,
        instance_uuid: str,
        execute_params: AnyExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> AnyExecutionResponse:
        """
        执行一个资源实例的核心业务逻辑，并返回AnyExecutionResponse。
        
        Args:
            instance_uuid: 目标资源实例UUID
            execute_params: 执行参数
            actor: 触发执行的操作者 (User)
            runtime_workspace: (可选) 明确指定的运行时工作空间。如果不传，实现层应回退到资源所属的 workspace。
        """
        raise NotImplementedError

    @abstractmethod
    async def execute_batch(
        self,
        instance_uuids: List[str],
        execute_params: AnyExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> List[AnyExecutionResponse]:
        """
        [NEW] 批量执行多个资源实例。
        
        默认实现可以简单地循环调用 execute (作为回退策略)，
        但具体的实现类（如 VectorDbService）应该重写此方法以实现特定的聚合优化。
        """
        # 默认实现：串行循环 (即使子类不优化也能工作)
        results = []
        for uuid in instance_uuids:
            res: AnyExecutionResponse = await self.execute(uuid, execute_params, actor, billing_entity)
            results.append(res)
        
        # 注意：这里返回的结构需要根据具体类型适配，这里仅为示意
        return results

ALL_RESOURCE_IMPLE_SERVICE: Dict[str, Type[ResourceImplementationService]] = {}

def register_service(cls: Type[ResourceImplementationService]):
    if not hasattr(cls, 'name') or not cls.name:
        raise ValueError(f"Service class {cls.__name__} must define a 'name' attribute.")
    if cls.name in ALL_RESOURCE_IMPLE_SERVICE:
        raise ValueError(f"Service with name '{cls.name}' already registered.")
    ALL_RESOURCE_IMPLE_SERVICE[cls.name] = cls
    return cls