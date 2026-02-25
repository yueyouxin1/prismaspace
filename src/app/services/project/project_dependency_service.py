# src/app/services/project/project_dependency_service.py

from collections import deque
from typing import Dict, List, Optional, Set, Tuple
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.core.context import AppContext
from app.models import User
from app.models.resource import Resource, ResourceInstance
from app.dao.project.project_dao import ProjectDao
from app.dao.project.project_resource_ref_dao import ProjectResourceRefDao
from app.schemas.project.project_dependency_schemas import (
    ProjectDependencyGraphRead,
    ProjectDependencyNodeRead,
    ProjectDependencyEdgeRead,
)
from app.services.exceptions import NotFoundError
from app.services.resource.base.base_resource_service import BaseResourceService


class ProjectDependencyService(BaseResourceService):
    MAX_TRANSITIVE_DEPTH = 8

    def __init__(self, context: AppContext):
        super().__init__(context)
        self.project_dao = ProjectDao(context.db)
        self.project_ref_dao = ProjectResourceRefDao(context.db)

    async def get_dependency_graph(self, project_uuid: str, actor: User) -> ProjectDependencyGraphRead:
        graph = await self._get_dependency_graph(project_uuid, actor)
        return graph

    async def _get_dependency_graph(self, project_uuid: str, actor: User) -> ProjectDependencyGraphRead:
        project = await self.project_dao.get_by_uuid(
            project_uuid,
            withs=[
                "workspace",
                {"name": "main_resource", "withs": ["resource_type", "workspace_instance"]},
            ],
        )
        if not project:
            raise NotFoundError("Project not found.")

        await self.context.perm_evaluator.ensure_can(["project:read"], target=project.workspace)

        refs = await self.project_ref_dao.list_by_project_id(project.id)

        declared_resource_uuids: Set[str] = set()
        node_tags: Dict[str, Set[str]] = {}
        node_resource_uuid_map: Dict[str, str] = {}
        all_resource_uuids: Set[str] = set()
        edges_map: Dict[Tuple[str, str, Optional[str], str, Optional[str]], ProjectDependencyEdgeRead] = {}
        instance_cache: Dict[str, Optional[ResourceInstance]] = {}
        queue: deque[Tuple[str, int, str]] = deque()
        processed: Set[str] = set()

        async def load_instance(instance_uuid: str) -> Optional[ResourceInstance]:
            if instance_uuid in instance_cache:
                return instance_cache[instance_uuid]
            try:
                instance = await self._get_full_instance_by_uuid(instance_uuid)
            except NotFoundError:
                instance = None
            instance_cache[instance_uuid] = instance
            return instance

        def add_node_tag(instance_uuid: str, *tags: str) -> None:
            if instance_uuid not in node_tags:
                node_tags[instance_uuid] = set()
            for tag in tags:
                if tag:
                    node_tags[instance_uuid].add(tag)

        # main_resource 也应纳入显式入口集合
        if project.main_resource and project.main_resource.workspace_instance:
            main_uuid = project.main_resource.workspace_instance.uuid
            add_node_tag(main_uuid, "explicit", "main-path")
            node_resource_uuid_map[main_uuid] = project.main_resource.uuid
            declared_resource_uuids.add(project.main_resource.uuid)
            all_resource_uuids.add(project.main_resource.uuid)
            queue.append((main_uuid, 0, "main-path"))

        for ref in refs:
            resource = ref.resource
            if not resource or not resource.workspace_instance:
                continue
            instance_uuid = resource.workspace_instance.uuid
            is_main = bool(project.main_resource_id and resource.id == project.main_resource_id)
            path_tag = "main-path" if is_main else "resource-path"
            add_node_tag(instance_uuid, "explicit", path_tag)
            node_resource_uuid_map[instance_uuid] = resource.uuid
            declared_resource_uuids.add(resource.uuid)
            all_resource_uuids.add(resource.uuid)
            queue.append((instance_uuid, 0, path_tag))

        while queue:
            source_instance_uuid, depth, chain_path = queue.popleft()
            if source_instance_uuid in processed:
                continue
            processed.add(source_instance_uuid)

            source_instance = await load_instance(source_instance_uuid)
            if not source_instance:
                add_node_tag(source_instance_uuid, "external")
                continue

            source_resource = source_instance.resource
            if source_resource:
                node_resource_uuid_map[source_instance_uuid] = source_resource.uuid
                all_resource_uuids.add(source_resource.uuid)

            impl_service = await self._get_impl_service_by_instance(source_instance)
            deps = await impl_service.get_dependencies(source_instance)

            for dep in deps:
                dep_instance_uuid = dep.instance_uuid
                dep_resource_uuid = dep.resource_uuid
                if dep_resource_uuid:
                    node_resource_uuid_map[dep_instance_uuid] = dep_resource_uuid
                    all_resource_uuids.add(dep_resource_uuid)

                add_node_tag(dep_instance_uuid, "implicit", chain_path)
                dep_instance = await load_instance(dep_instance_uuid)
                is_external = (
                    dep_instance is None
                    or dep_instance.resource is None
                    or dep_instance.resource.workspace_id != project.workspace_id
                )
                if is_external:
                    add_node_tag(dep_instance_uuid, "external")

                source_tags = node_tags.get(source_instance_uuid, set())
                relation_type = "explicit" if "explicit" in source_tags else "implicit"
                relation_path = "external" if is_external else chain_path
                edge_key = (
                    source_instance_uuid,
                    dep_instance_uuid,
                    dep.alias,
                    relation_type,
                    relation_path,
                )
                if edge_key not in edges_map:
                    edges_map[edge_key] = ProjectDependencyEdgeRead(
                        source_instance_uuid=source_instance_uuid,
                        target_instance_uuid=dep_instance_uuid,
                        alias=dep.alias,
                        relation_type=relation_type,
                        relation_path=relation_path,
                    )

                if not is_external and depth < self.MAX_TRANSITIVE_DEPTH:
                    queue.append((dep_instance_uuid, depth + 1, chain_path))

        resources_by_uuid: Dict[str, Resource] = {}
        if all_resource_uuids:
            stmt = (
                select(Resource)
                .where(Resource.uuid.in_(all_resource_uuids))
                .options(joinedload(Resource.resource_type))
            )
            result = await self.db.execute(stmt)
            resources_by_uuid = {resource.uuid: resource for resource in result.scalars().all()}

        nodes_map: Dict[str, ProjectDependencyNodeRead] = {}
        for instance_uuid, tags in node_tags.items():
            resource_uuid = node_resource_uuid_map.get(instance_uuid, "")
            resource = resources_by_uuid.get(resource_uuid)
            external = "external" in tags
            nodes_map[instance_uuid] = ProjectDependencyNodeRead(
                resource_uuid=resource_uuid,
                instance_uuid=instance_uuid,
                name=resource.name if resource else None,
                resource_type=resource.resource_type.name if resource and resource.resource_type else None,
                declared="explicit" in tags,
                external=external,
                node_tags=sorted(tags),
            )

        return ProjectDependencyGraphRead(
            nodes=list(nodes_map.values()),
            edges=list(edges_map.values()),
        )
