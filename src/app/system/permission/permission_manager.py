# src/app/system/permission/permission_manager.py

import re
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.models import ActionPermission
from app.dao.permission.action_permission_dao import ActionPermissionDao
from app.schemas.permission.permission_schemas import PermissionCreate, PermissionUpdate, PermissionCreateNode, PermissionReadNode
from app.services.exceptions import ServiceException, NotFoundError

class PermissionManager:
    """
    [系统层] 权限定义的核心业务逻辑。
    不处理权限检查，只关注数据操作。
    """
    # 定义一个正则表达式用于验证权限名称的格式
    _VALID_NAME_REGEX = re.compile(r"^[a-z0-9_-]+(:[a-z0-9_-]+)*$")

    def __init__(self, db: AsyncSession):
        self.db = db
        self.dao = ActionPermissionDao(db)

    def _validate_permission_name(self, name: str):
        """
        [增强] 验证权限名称格式是否有效。
        一个有效的名称应该由小写字母、数字、下划线、连字符组成，并用冒号分隔。
        例如: 'platform:permission:manage' 或 'project:read'
        """
        if not self._VALID_NAME_REGEX.match(name):
            raise ServiceException(
                f"Invalid permission name format: '{name}'. "
                "Name must consist of lowercase letters, numbers, and underscores, separated by colons."
            )

    def _build_dto_tree_from_flat_list(self, permissions: List[PermissionReadNode]) -> List[PermissionReadNode]:
        """
        [核心树构建逻辑] 从一个扁平的 Pydantic DTO 列表中构建嵌套的树结构。
        此函数操作的是与数据库会话完全解耦的对象，绝对安全。
        """
        perm_map: Dict[int, PermissionReadNode] = {perm.id: perm for perm in permissions}
        root_nodes: List[PermissionReadNode] = []

        for perm_dto in permissions:
            if perm_dto.parent_id and perm_dto.parent_id in perm_map:
                parent_dto = perm_map[perm_dto.parent_id]
                parent_dto.children.append(perm_dto)
            else:
                root_nodes.append(perm_dto)
        
        return root_nodes

    async def get_permission_tree(self) -> List[PermissionReadNode]:
        """
        [健壮版] 获取完整的权限树 DTO。
        1. 从数据库一次性加载所有扁平的 ORM 对象。
        2. 立即将它们转换为 DTO 列表。
        3. 在安全的 DTO 列表上构建树。
        """
        all_orm_permissions = await self.dao.get_list()
        # [关键步骤] 立即转换为 DTO，从而与会话解耦
        flat_permission_dtos = [PermissionReadNode.model_validate(p) for p in all_orm_permissions]
        return self._build_dto_tree_from_flat_list(flat_permission_dtos)

    async def get_assignable_permission_tree(self) -> List[PermissionReadNode]:
        """
        [健壮版] 获取所有可分配给团队角色的权限树 DTO。
        """
        assignable_orm_permissions = await self.dao.get_list(where={"is_assignable": True})
        flat_assignable_dtos = [PermissionReadNode.model_validate(p) for p in assignable_orm_permissions]
        return self._build_dto_tree_from_flat_list(flat_assignable_dtos)

    async def create_permission(self, perm_data: PermissionCreate) -> ActionPermission:
        """递归地创建一个新的权限节点或树。"""
        parent_id = None
        if perm_data.parent_name:
            self._validate_permission_name(perm_data.parent_name)
            parent_perm = await self.dao.get_one(where={"name": perm_data.parent_name})
            if not parent_perm:
                raise NotFoundError(f"Parent permission '{perm_data.parent_name}' not found.")
            parent_id = parent_perm.id
        
        # 委托给内部递归函数
        return await self._create_permission_node(perm_data, parent_id)

    async def _create_permission_node(self, node_data: PermissionCreateNode, parent_id: Optional[int]) -> ActionPermission:
        """
        内部辅助函数，用于创建单个权限节点及其子节点。
        整个过程是原子性的，任何失败都会导致事务回滚。
        """
        self._validate_permission_name(node_data.name)
        existing = await self.dao.get_one(where={"name": node_data.name})
        if existing:
            raise ServiceException(f"Permission with name '{node_data.name}' already exists.")

        new_perm = ActionPermission(
            name=node_data.name,
            label=node_data.label,
            description=node_data.description,
            type=node_data.type,
            is_assignable=node_data.is_assignable,
            parent_id=parent_id
        )
        self.db.add(new_perm)
        # 必须在这里 flush，以便为子节点的 parent_id 获取到 new_perm.id
        await self.db.flush()

        for child_data in node_data.children:
            await self._create_permission_node(child_data, new_perm.id)
        
        # 刷新以加载在递归中创建的 children 关系
        await self.db.refresh(new_perm, attribute_names=['children'])
        return new_perm

    async def update_permission(self, name: str, update_data: PermissionUpdate) -> ActionPermission:
        """
        更新一个权限的元数据。
        [设计决策] 此方法不允许更改权限的父子关系（即移动节点），
        因为这需要复杂的循环依赖检查。此功能可在未来版本中添加。
        """
        perm_to_update = await self.dao.get_one(where={"name": name})
        if not perm_to_update:
            raise NotFoundError(f"Permission '{name}' not found.")
            
        update_dict = update_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(perm_to_update, key, value)
            
        await self.db.flush()
        await self.db.refresh(perm_to_update)
        return perm_to_update

    async def delete_permission(self, name: str) -> None:
        """
        删除一个权限及其所有子权限。
        [说明] 这是一个健壮的操作。模型上的 cascade="all, delete-orphan"
        确保了所有子权限对象会被一并删除。同时，数据库的外键约束
        将自动清理 'role_permissions' 表中所有对已删除权限的引用。
        """
        perm_to_delete = await self.dao.get_one(where={"name": name})
        if not perm_to_delete:
            raise NotFoundError(f"Permission '{name}' not found.")
        
        await self.db.delete(perm_to_delete)
        await self.db.flush()