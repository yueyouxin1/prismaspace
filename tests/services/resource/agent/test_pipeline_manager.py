from types import SimpleNamespace

from app.engine.model.llm import LLMMessage
from app.schemas.resource.agent.agent_schemas import AgentConfig
from app.services.resource.agent.pipeline_manager import AgentPipelineManager
import app.services.resource.agent.pipeline_manager as pipeline_module


def _stub_processor(tag: str):
    class _Processor:
        def __init__(self, *args, **kwargs):
            self.tag = tag

        async def process(self, *args, **kwargs):
            return None

    return _Processor


def test_pipeline_manager_appends_custom_history_processor_last(monkeypatch):
    monkeypatch.setattr(pipeline_module, "ShortContextProcessor", _stub_processor("short"))
    monkeypatch.setattr(pipeline_module, "RAGContextProcessor", _stub_processor("rag"))
    monkeypatch.setattr(pipeline_module, "DeepMemoryProcessor", _stub_processor("deep"))
    monkeypatch.setattr(pipeline_module, "CustomHistoryMergeProcessor", _stub_processor("custom"))
    monkeypatch.setattr(pipeline_module, "ToolChainAlignmentProcessor", _stub_processor("align"))
    monkeypatch.setattr(pipeline_module, "DependencySkillsProcessor", _stub_processor("dep-skill"))
    monkeypatch.setattr(pipeline_module, "DeepMemorySkillsProcessor", _stub_processor("deep-skill"))
    monkeypatch.setattr(pipeline_module, "MemoryVarSkillsProcessor", _stub_processor("mem-skill"))

    manager = AgentPipelineManager(
        system_message=LLMMessage(role="system", content="sys"),
        user_message=LLMMessage(role="user", content="hi"),
        history=[LLMMessage(role="assistant", content="history")],
        tool_executor=SimpleNamespace(get_llm_tools=lambda: []),
    )
    config = AgentConfig()
    config.deep_memory.enabled = True

    manager.add_standard_processors(
        app_context=SimpleNamespace(),
        agent_config=config,
        dependencies=[],
        runtime_workspace=SimpleNamespace(),
        session_manager=SimpleNamespace(),
        prompt_variables=None,
    )

    assert [processor.tag for processor in manager._context_processors] == [
        "short",
        "rag",
        "deep",
        "custom",
        "align",
    ]


def test_pipeline_manager_always_appends_tool_chain_alignment(monkeypatch):
    monkeypatch.setattr(pipeline_module, "ShortContextProcessor", _stub_processor("short"))
    monkeypatch.setattr(pipeline_module, "RAGContextProcessor", _stub_processor("rag"))
    monkeypatch.setattr(pipeline_module, "DeepMemoryProcessor", _stub_processor("deep"))
    monkeypatch.setattr(pipeline_module, "CustomHistoryMergeProcessor", _stub_processor("custom"))
    monkeypatch.setattr(pipeline_module, "ToolChainAlignmentProcessor", _stub_processor("align"))
    monkeypatch.setattr(pipeline_module, "DependencySkillsProcessor", _stub_processor("dep-skill"))
    monkeypatch.setattr(pipeline_module, "DeepMemorySkillsProcessor", _stub_processor("deep-skill"))
    monkeypatch.setattr(pipeline_module, "MemoryVarSkillsProcessor", _stub_processor("mem-skill"))

    manager = AgentPipelineManager(
        system_message=LLMMessage(role="system", content="sys"),
        user_message=LLMMessage(role="user", content="hi"),
        history=[],
        tool_executor=SimpleNamespace(get_llm_tools=lambda: []),
    )
    config = AgentConfig()
    config.deep_memory.enabled = False

    manager.add_standard_processors(
        app_context=SimpleNamespace(),
        agent_config=config,
        dependencies=[],
        runtime_workspace=SimpleNamespace(),
        session_manager=None,
        prompt_variables=None,
    )

    assert [processor.tag for processor in manager._context_processors] == [
        "rag",
        "align",
    ]


def test_pipeline_manager_skips_deep_memory_skill_without_session_manager(monkeypatch):
    monkeypatch.setattr(pipeline_module, "ShortContextProcessor", _stub_processor("short"))
    monkeypatch.setattr(pipeline_module, "RAGContextProcessor", _stub_processor("rag"))
    monkeypatch.setattr(pipeline_module, "DeepMemoryProcessor", _stub_processor("deep"))
    monkeypatch.setattr(pipeline_module, "CustomHistoryMergeProcessor", _stub_processor("custom"))
    monkeypatch.setattr(pipeline_module, "ToolChainAlignmentProcessor", _stub_processor("align"))
    monkeypatch.setattr(pipeline_module, "DependencySkillsProcessor", _stub_processor("dep-skill"))
    monkeypatch.setattr(pipeline_module, "DeepMemorySkillsProcessor", _stub_processor("deep-skill"))
    monkeypatch.setattr(pipeline_module, "MemoryVarSkillsProcessor", _stub_processor("mem-skill"))

    manager = AgentPipelineManager(
        system_message=LLMMessage(role="system", content="sys"),
        user_message=LLMMessage(role="user", content="hi"),
        history=[],
        tool_executor=SimpleNamespace(get_llm_tools=lambda: []),
    )
    config = AgentConfig()
    config.deep_memory.enabled = True

    manager.add_standard_processors(
        app_context=SimpleNamespace(),
        agent_config=config,
        dependencies=[],
        runtime_workspace=SimpleNamespace(),
        session_manager=None,
        prompt_variables=None,
    )

    assert [processor.tag for processor in manager._skill_processors] == [
        "dep-skill",
    ]
