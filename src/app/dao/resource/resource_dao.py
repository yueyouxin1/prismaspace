# app/dao/resource/resource_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload, load_only, lazyload
from typing import Optional, List
from app.dao.base_dao import BaseDao
from app.models.workspace import Workspace
from app.models.identity import User
from app.models.resource import Resource, ResourceInstance, ResourceType
from app.services.exceptions import NotFoundError


def _instance_pointer_loader(relation_attr, use_joined: bool = False):
    """
    仅加载 ResourceInstance 的轻量指针字段，避免深层 joined eager 链路。
    """
    loader = joinedload if use_joined else selectinload
    return (
        loader(relation_attr)
        .options(
            load_only(
                ResourceInstance.id,
                ResourceInstance.uuid,
                ResourceInstance.resource_type,
                ResourceInstance.status
            ),
            lazyload(ResourceInstance.resource),
            lazyload(ResourceInstance.creator),
            lazyload(ResourceInstance.linked_feature),
        )
    )

class ResourceDao(BaseDao[Resource]):
    # 向后兼容：保留这些属性，避免调用方改动。
    workspace_instance_loaders = [selectinload(Resource.workspace_instance)]
    latest_published_instance_loaders = [selectinload(Resource.latest_published_instance)]
    instance_versions_loaders = [selectinload(Resource.instance_versions)]

    # 热路径（列表/详情）使用轻量 instance 指针加载器。
    instance_pointer_loaders = [
        _instance_pointer_loader(Resource.workspace_instance),
        _instance_pointer_loader(Resource.latest_published_instance),
    ]
    # 单资源详情使用 joinedload，减少 selectin 的额外 round-trip。
    detail_instance_pointer_loaders = [
        _instance_pointer_loader(Resource.workspace_instance, use_joined=True),
        _instance_pointer_loader(Resource.latest_published_instance, use_joined=True),
    ]
    
    def __init__(self, db_session: AsyncSession):
        super().__init__(Resource, db_session)

    def _normalize_withs(self, withs: Optional[list]) -> Optional[list]:
        """
        将常用 instance 关系转换为轻量加载器，避免误触发重查询。
        """
        if withs is None:
            return None

        normalized = []
        for item in withs:
            if item == "workspace_instance":
                normalized.append(_instance_pointer_loader(Resource.workspace_instance, use_joined=True))
            elif item == "latest_published_instance":
                normalized.append(_instance_pointer_loader(Resource.latest_published_instance, use_joined=True))
            else:
                normalized.append(item)
        return normalized

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Resource]:
        """Finds a resource by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=self._normalize_withs(withs))

    async def get_by_pk(
        self,
        pk_value: int,
        joins: Optional[list] = None,
        withs: Optional[list] = None,
        fields: Optional[list[str]] = None,
        options: Optional[List] = None
    ) -> Optional[Resource]:
        return await super().get_by_pk(
            pk_value=pk_value,
            joins=joins,
            withs=self._normalize_withs(withs),
            fields=fields,
            options=options
        )

    async def get_one(
        self,
        where: Optional[dict | list] = None,
        where_or: Optional[list] = None,
        joins: Optional[list] = None,
        withs: Optional[list] = None,
        fields: Optional[list[str]] = None,
        options: Optional[List] = None,
        order: Optional[list] = None
    ) -> Optional[Resource]:
        return await super().get_one(
            where=where,
            where_or=where_or,
            joins=joins,
            withs=self._normalize_withs(withs),
            fields=fields,
            options=options,
            order=order
        )

    async def get_list(
        self,
        where: Optional[dict | list] = None,
        where_or: Optional[list] = None,
        joins: Optional[list] = None,
        withs: Optional[list] = None,
        fields: Optional[list[str]] = None,
        options: Optional[List] = None,
        order: Optional[list] = None,
        page: int = 0,
        limit: int = 0,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        time_key: str = "created_at",
        unique: bool = False
    ) -> list[Resource]:
        return await super().get_list(
            where=where,
            where_or=where_or,
            joins=joins,
            withs=self._normalize_withs(withs),
            fields=fields,
            options=options,
            order=order,
            page=page,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
            time_key=time_key,
            unique=unique
        )

    async def get_resource_details_by_uuid(self, resource_uuid: str) -> Optional[Resource]:
        """
        获取详情时只加载 instance 指针字段，完整实例由实现层按类型单独加载。
        """
        stmt = (
            select(Resource)
            .where(Resource.uuid == resource_uuid)
            .options(
                lazyload("*"),
                joinedload(Resource.workspace).options(
                    lazyload("*"),
                    load_only(
                        Workspace.id,
                        Workspace.uuid,
                        Workspace.owner_user_id,
                        Workspace.owner_team_id
                    )
                ),
                joinedload(Resource.creator).options(
                    lazyload("*"),
                    load_only(User.id, User.uuid, User.nick_name, User.avatar)
                ),
                joinedload(Resource.resource_type).options(
                    lazyload("*"),
                    load_only(ResourceType.id, ResourceType.name)
                ),
                *self.detail_instance_pointer_loaders
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().first()

    async def get_resources_by_workspace_id(self, workspace_id: int) -> List[Resource]:
        """
        获取工作空间资源列表。
        仅预加载实例指针字段（uuid/type/status），避免全量 instance 开销。
        """
        stmt = (
            select(Resource)
            .where(Resource.workspace_id == workspace_id)
            .options(
                lazyload("*"),
                joinedload(Resource.creator).options(
                    lazyload("*"),
                    load_only(User.id, User.uuid, User.nick_name, User.avatar)
                ),
                joinedload(Resource.resource_type).options(
                    lazyload("*"),
                    load_only(ResourceType.id, ResourceType.name)
                ),
                *self.instance_pointer_loaders
            )
            .order_by(self.model.created_at.desc())
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().all()

class ResourceInstanceDao(BaseDao[ResourceInstance]):
    def __init__(self, db_session: AsyncSession):
        # [关键优化] 默认走基表查询，避免每次 instance 查询都 LEFT JOIN 所有子类表。
        super().__init__(ResourceInstance, db_session)

    @staticmethod
    def _lightweight_options() -> list:
        # 关闭模型上默认的 joined eager，按需加载。
        return [lazyload("*")]

    @staticmethod
    def _permission_path_option():
        # 鉴权最小链路：instance -> resource -> workspace（仅 owner 关键字段）
        resource_loader = joinedload(ResourceInstance.resource).options(
            lazyload("*"),
            load_only(
                Resource.id,
                Resource.uuid,
                Resource.workspace_id,
                Resource.resource_type_id
            )
        )
        return resource_loader.joinedload(Resource.workspace).options(
            lazyload("*"),
            load_only(
                Workspace.id,
                Workspace.uuid,
                Workspace.owner_user_id,
                Workspace.owner_team_id
            )
        )

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[ResourceInstance]:
        stmt = (
            select(ResourceInstance)
            .where(ResourceInstance.uuid == uuid)
            .options(
                *self._lightweight_options(),
                self._permission_path_option(),
            )
        )
        if withs:
            stmt = self._withs(stmt, withs)

        result = await self.db_session.execute(stmt)
        return result.scalars().first()

    async def get_by_pk(
        self,
        pk_value: int,
        joins: Optional[list] = None,
        withs: Optional[list] = None,
        fields: Optional[list] = None,
        options: Optional[List] = None
    ) -> Optional[ResourceInstance]:
        merged_options = [*self._lightweight_options()]
        if options:
            merged_options.extend(options)
        return await super().get_by_pk(
            pk_value=pk_value,
            joins=joins,
            withs=withs,
            fields=fields,
            options=merged_options
        )

    async def get_one(
        self,
        where: Optional[dict | list] = None,
        where_or: Optional[list] = None,
        joins: Optional[list] = None,
        withs: Optional[list] = None,
        fields: Optional[list[str]] = None,
        options: Optional[List] = None,
        order: Optional[list] = None
    ) -> Optional[ResourceInstance]:
        merged_options = [*self._lightweight_options()]
        if options:
            merged_options.extend(options)
        return await super().get_one(
            where=where,
            where_or=where_or,
            joins=joins,
            withs=withs,
            fields=fields,
            options=merged_options,
            order=order
        )

    async def get_list(
        self,
        where: Optional[dict | list] = None,
        where_or: Optional[list] = None,
        joins: Optional[list] = None,
        withs: Optional[list] = None,
        fields: Optional[list[str]] = None,
        options: Optional[List] = None,
        order: Optional[list] = None,
        page: int = 0,
        limit: int = 0,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        time_key: str = "created_at",
        unique: bool = False
    ) -> list[ResourceInstance]:
        merged_options = [*self._lightweight_options()]
        if options:
            merged_options.extend(options)
        return await super().get_list(
            where=where,
            where_or=where_or,
            joins=joins,
            withs=withs,
            fields=fields,
            options=merged_options,
            order=order,
            page=page,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
            time_key=time_key,
            unique=unique
        )

    async def list_uuids_by_resource_id(self, resource_id: int) -> List[str]:
        stmt = (
            select(ResourceInstance.uuid)
            .where(ResourceInstance.resource_id == resource_id)
            .order_by(ResourceInstance.created_at.desc())
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().all()

    async def list_version_summaries_by_resource_id(self, resource_id: int) -> List[dict]:
        stmt = (
            select(
                ResourceInstance.uuid,
                ResourceInstance.version_tag,
                ResourceInstance.status,
                ResourceInstance.created_at
            )
            .where(ResourceInstance.resource_id == resource_id)
            .order_by(ResourceInstance.created_at.desc())
        )
        result = await self.db_session.execute(stmt)
        rows = result.all()
        return [
            {
                "uuid": row.uuid,
                "version_tag": row.version_tag,
                "status": row.status.value if row.status else None,
                "created_at": row.created_at
            }
            for row in rows
        ]

    async def get_type_by_uuid(self, instance_uuid: str) -> str:
        stmt = select(ResourceInstance.resource_type).where(ResourceInstance.uuid == instance_uuid)
        resource_type = await self.db_session.scalar(stmt)
        
        if resource_type is None:
            raise NotFoundError(f"Resource instance type not found")
            
        return resource_type

    async def get_runtime_by_uuid(self, instance_uuid: str) -> ResourceInstance:
        """
        获取运行时必要的数据
        这里必须加载 workspace 所有者。
        """
        instance = await self.get_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Resource instance not found.")
        return instance
