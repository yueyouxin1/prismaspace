# src/app/schemas/project/project_dependency_schemas.py

from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class ProjectDependencyNodeRead(BaseModel):
    resource_uuid: str
    instance_uuid: str
    name: Optional[str] = None
    resource_type: Optional[str] = None
    declared: bool
    external: bool
    node_tags: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ProjectDependencyEdgeRead(BaseModel):
    source_instance_uuid: str
    target_instance_uuid: str
    alias: Optional[str] = None
    relation_type: str = "implicit"
    relation_path: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ProjectDependencyGraphRead(BaseModel):
    nodes: List[ProjectDependencyNodeRead]
    edges: List[ProjectDependencyEdgeRead]

    model_config = ConfigDict(from_attributes=True)
