# src/app/system/module/service_module_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from app.constants.permission_constants import SERVICE_MODULE_ROOT_PERM
from app.constants.role_constants import ROLE_PLAN_FREE, ROLE_TEAM_MEMBER
from app.models import ServiceModule, ServiceModuleVersion, ActionPermission, ActionPermissionType
from app.dao.module.service_module_dao import ServiceModuleTypeDao, ServiceModuleProviderDao, ServiceModuleDao, ServiceModuleVersionDao
from app.dao.permission.action_permission_dao import ActionPermissionDao
from app.schemas.module.service_module_schemas import ServiceModuleCreateFull, ServiceModuleCreate, ServiceModuleVersionCreate
from app.schemas.permission.permission_schemas import PermissionCreate
from app.schemas.permission.role_schemas import RoleUpdate
from app.system.permission.permission_manager import PermissionManager
from app.system.permission.role_manager import RoleManager
from app.services.module.types.specifications import get_spec_models
from app.services.exceptions import ServiceException, NotFoundError, ConfigurationError

class ServiceModuleManager:
    """
    [System Layer] Manages the atomic, core business logic for ServiceModules.
    This class does not handle authorization.
    """
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dao = ServiceModuleDao(db)
        self.version_dao = ServiceModuleVersionDao(db)
        self.type_dao = ServiceModuleTypeDao(db)
        self.provider_dao = ServiceModuleProviderDao(db)
        self.perm_dao = ActionPermissionDao(db)
        self.permission_manager = PermissionManager(db)
        self.role_manager = RoleManager(db)

    async def create_module(self, module_data: ServiceModuleCreate) -> ServiceModule:
        """
        [Seed/System] 获取或创建 ServiceModule。
        如果是新创建，会自动处理 Permission。
        """
        # 1. 检查是否存在
        existing = await self.dao.get_one(where={"name": module_data.name})
        if existing:
            raise ServiceException(f"ServiceModule with name '{module_data.name}' already exists.")

        # 2. 如果不存在，创建新的
        module_type = await self.type_dao.get_one(where={"name": module_data.type_name})
        if not module_type:
            raise NotFoundError(f"ServiceModuleType with name '{module_data.type_name}' not found.")
        module_provider = await self.provider_dao.get_one(where={"name": module_data.provider_name})
        if not module_provider:
            raise NotFoundError(f"ServiceModuleProvider with name '{module_data.provider_name}' not found.")
        
        perm_name = f"{SERVICE_MODULE_ROOT_PERM}:use:{module_provider.name}:{module_data.name}"
        perm_create_schema = PermissionCreate(
            name=perm_name,
            label=f"Use {module_data.label}",
            description=f"Allows usage of the {module_data.label} service module.",
            type=ActionPermissionType.API,
            is_assignable=True,
            parent_name=SERVICE_MODULE_ROOT_PERM
        )
        new_permission = await self.permission_manager.create_permission(perm_create_schema)

        module_create_dict = module_data.model_dump(exclude={'type_name', 'provider_name'})
        new_module = ServiceModule(
            **module_create_dict,
            type_id=module_type.id,
            provider_id=module_provider.id,
            permission_id=new_permission.id
        )
        self.db.add(new_module)
        await self.db.flush()

        # Grant the new permission to BOTH base roles
        base_role_names = [ROLE_PLAN_FREE, ROLE_TEAM_MEMBER]
        for role_name in base_role_names:
            base_role = await self.role_manager.role_dao.get_system_role_by_name(role_name, withs=['permissions'])
            if not base_role:
                # This is a critical configuration error, should fail loudly
                raise ConfigurationError(f"System base role '{role_name}' not found. Seeding is required.")
            
            # Get existing direct permissions and add the new one
            # Note: `update_role` expects the full set of direct permissions.
            existing_perm_names = {p.name for p in base_role.permissions}
            
            # Check if the permission is already there to be idempotent
            if perm_name not in existing_perm_names:
                # The RoleUpdate schema expects a list of permission names.
                # We pass the full new set of direct permissions.
                update_schema = RoleUpdate(permissions=[*existing_perm_names, perm_name])
                await self.role_manager.update_role(base_role, update_schema)

        # Re-fetch the complete object graph after the transaction
        final_module = await self.dao.get_one(
            where={"id": new_module.id},
            withs=["type", "versions", "permission"]
        )
        return final_module

    async def delete_module(self, module_id: int):
        """物理删除 Module 及其所有 Versions (Cascade)"""
        module = await self.dao.get_by_pk(module_id)
        if not module:
            raise NotFoundError("Module not found.")
        
        # DB级联删除会处理 Versions, 但我们需要先获取它们以便上层处理副作用
        # 这里我们只做 DB 删除
        await self.db.delete(module)
        await self.db.flush()

    async def create_version(self, module: ServiceModule, version_data: ServiceModuleVersionCreate) -> ServiceModuleVersion:
        """
        [Seed/System] 确保版本存在。如果不存在则创建。
        """
        # 1. 检查版本是否存在 (根据 module_id 和 version_tag)
        existing = await self.version_dao.get_one(where={
            "service_module_id": module.id,
            "name": version_data.name
        })
        if existing:
            raise ServiceException(f"ServiceModuleVersion with name '{version_data.name}' already exists.")
            
        # 2. 创建新版本
        try:
            spec_models = get_spec_models(module.type.name)
            # Pydantic's discriminated union already did the validation, 
            # but this is an extra layer of programmatic check.
            if not isinstance(version_data.attributes, spec_models['attributes']):
                raise ServiceException(f"'attributes' shape does not match type '{module.type.name}'.")
            if not isinstance(version_data.config, spec_models['config']):
                raise ServiceException(f"'config' shape does not match type '{module.type.name}'.")
        except ValueError as e:
                raise ConfigurationError(str(e))
        
        is_default = version_data.is_default

        new_version = ServiceModuleVersion(
            **version_data.model_dump(exclude={'is_default', 'attributes', 'config'}),
            attributes=version_data.attributes.model_dump(mode='json'),
            config=version_data.config.model_dump(mode='json'),
            service_module_id=module.id
        )
        self.db.add(new_version)
        await self.db.flush()
        
        # 3. 更新 Module 的 latest_version 指针 (简单的策略：总是指向最新添加的)
        module.latest_version_id = new_version.id
        await self.db.flush()
        if is_default:
            await self.set_type_default_version(module.type_id, new_version.id)
        
        return new_version

    async def delete_version(self, version_id: int):
        """物理删除 Version"""
        version = await self.version_dao.get_by_pk(version_id)
        if not version:
            raise NotFoundError("Version not found.")
        
        await self.db.delete(version)
        await self.db.flush()
        
    async def set_type_default_version(self, type_id: int, version_id: int):
        """
        [Seed/Admin] 设置某类型的默认版本。
        """
        module_type = await self.type_dao.get_by_pk(type_id)
        module_type.default_version_id = version_id
        await self.db.flush()

    async def create_module_with_versions(
        self, 
        module_data: ServiceModuleCreateFull
    ) -> ServiceModule:
        async with self.db.begin_nested():
            module = await self.create_module(module_data.module)
            for version_data in module_data.versions:
                await self.create_version(module, version_data)
            return module