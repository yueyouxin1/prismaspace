from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.resource.knowledge.knowledge_schemas import (
    KnowledgeBaseExecutionParams,
    KnowledgeBaseExecutionRequest,
)
from app.services.resource.knowledge.knowledge_service import KnowledgeBaseService


pytestmark = pytest.mark.asyncio


class _FakeAsyncSession:
    def __init__(self):
        self.added = []

    def add_all(self, items):
        self.added.extend(items)

    async def flush(self):
        return None


async def test_knowledge_single_instance_trace_sets_target_instance_id():
    service = object.__new__(KnowledgeBaseService)
    db = _FakeAsyncSession()
    actor = SimpleNamespace(id=1)
    workspace = SimpleNamespace(id=99)
    instance = SimpleNamespace(
        id=42,
        uuid="kb-1",
        engine_alias="vec",
        embedding_module_version_id=7,
        resource=SimpleNamespace(workspace=workspace),
    )

    service.db = db
    service.context = SimpleNamespace(vector_manager=SimpleNamespace(), actor=actor)
    service.dao = SimpleNamespace(get_by_uuids=AsyncMock(return_value=[instance]))
    service._check_execute_perm = AsyncMock(return_value=None)
    service._get_version_document_manifest = AsyncMock(return_value=set())
    service.embedding_service = SimpleNamespace(generate_embedding=AsyncMock())
    service.chunk_dao = SimpleNamespace(get_list=AsyncMock(return_value=[]))

    request = KnowledgeBaseExecutionRequest(
        inputs=KnowledgeBaseExecutionParams(query="hello"),
    )

    responses = await service.execute_batch(["kb-1"], request, actor, runtime_workspace=workspace)

    assert len(responses) == 1
    assert len(db.added) == 1
    assert db.added[0].operation_name == "knowledge.batch_search"
    assert db.added[0].target_instance_id == 42


async def test_knowledge_multi_instance_trace_keeps_ambiguous_target_empty():
    service = object.__new__(KnowledgeBaseService)
    db = _FakeAsyncSession()
    actor = SimpleNamespace(id=1)
    workspace = SimpleNamespace(id=99)
    instances = [
        SimpleNamespace(
            id=42,
            uuid="kb-1",
            engine_alias="vec",
            embedding_module_version_id=7,
            resource=SimpleNamespace(workspace=workspace),
        ),
        SimpleNamespace(
            id=43,
            uuid="kb-2",
            engine_alias="vec",
            embedding_module_version_id=7,
            resource=SimpleNamespace(workspace=workspace),
        ),
    ]

    service.db = db
    service.context = SimpleNamespace(vector_manager=SimpleNamespace(), actor=actor)
    service.dao = SimpleNamespace(get_by_uuids=AsyncMock(return_value=instances))
    service._check_execute_perm = AsyncMock(return_value=None)
    service._get_version_document_manifest = AsyncMock(return_value=set())
    service.embedding_service = SimpleNamespace(generate_embedding=AsyncMock())
    service.chunk_dao = SimpleNamespace(get_list=AsyncMock(return_value=[]))

    request = KnowledgeBaseExecutionRequest(
        inputs=KnowledgeBaseExecutionParams(query="hello"),
    )

    responses = await service.execute_batch(["kb-1", "kb-2"], request, actor, runtime_workspace=workspace)

    assert len(responses) == 2
    assert len(db.added) == 1
    assert db.added[0].target_instance_id is None
