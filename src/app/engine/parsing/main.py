# engine/parsing/main.py
import asyncio
from typing import Optional, Type, List, Dict, Any, Literal, NamedTuple, Union
from .base import BaseEngine, BasePolicy, ParserPolicy, ChunkerPolicy, BaseParser, BaseChunker, Document, DocumentChunk, ALL_PARSERS, ALL_CHUNKERS, ALL_MIME_TYPE
from .utils import get_file_bytes_and_mime, get_mime_by_file_bytes

# 示例运行时配置：
base_policy = {
    "parser": {
        "parser_name": "simple_parser_v1",
        "allowed_mime_types": ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        "params": {}
    },
    "chunkers": [
        {
            "chunker_name": "simple_chunker_v1",
            "params": {"chunk_size": 600}
        },
        {
            "chunker_name": "xml_chunker_v1",
            "params": {}
        },
        {
            "chunker_name": "qa_chunker_v1",
            "params": {"model": "gpt-4o"}
        }
    ]
}

class ParsingEngine:
    """纯粹的解析引擎"""
    def __init__(self):
        self._parsers_cache: Dict[str, BaseParser] = {}

    def _get_parser(self, parser_name: str) -> BaseParser:
        if parser_name in self._parsers_cache:
            return self._parsers_cache[parser_name]

        parser_class = ALL_PARSERS.get(parser_name)
        
        if not parser_class:
            raise ValueError(f"No parser registered for parser name '{parser_name}'.")

        print(f"Lazy loading and caching parser for parser name: '{parser_name}'")
        parser_instance = parser_class()

        self._parsers_cache[parser_name] = parser_instance
        
        return parser_instance

    async def execute(
        self,
        policy: ParserPolicy,
        file_content: Any = None,
        mime_type: str = None,
    ) -> Document:

        parser_name = policy.parser_name
        parser = self._get_parser(parser_name)

        empty_document = Document(content="", content_type="text", mime_type="", source_parser="", metadata={})

        if mime_type not in policy.allowed_mime_types:
            return empty_document

        if mime_type not in parser.support_mime_types:
            return empty_document

        try:
            # 调用具体的解析器，并传递特定参数
            # 第一阶段输出文本内容
            return await parser.run(
                file_content=file_content,
                mime_type=mime_type,
                **policy.params
            )
        except Exception as e:
            raise ValueError(f"Error executing parser '{parser_name}': {e}")

class ChunkingEngine:
    """纯粹的分块引擎"""
    def __init__(self):
        self._chunkers_cache: Dict[str, BaseChunker] = {}

    def _get_chunker(self, chunker_name: str) -> BaseChunker:
        if chunker_name in self._chunkers_cache:
            return self._chunkers_cache[chunker_name]

        chunker_class = ALL_CHUNKERS.get(chunker_name)
        
        if not chunker_class:
            raise ValueError(f"No chunker registered for chunker name '{chunker_name}'.")

        print(f"Lazy loading and caching chunker for parser name: '{chunker_name}'")
        chunker_instance = chunker_class()

        self._chunkers_cache[chunker_name] = chunker_instance
        
        return chunker_instance

    async def execute(self, document: Document, policy: ChunkerPolicy) -> List[DocumentChunk]:
        chunker_name = policy.chunker_name
        chunker = self._get_chunker(chunker_name)
        try:
            # 调用具体的分块器，并传递特定参数
            # 第二阶段生成分块列表  
            return await chunker.run(
                document=document,
                **policy.params
            )
            # [未来] 如果是串行，可以 current_input_doc = chunks ...
        except Exception as e:
            raise ValueError(f"Error executing chunker '{chunker_name}': {e}")
            # 可以在这里添加更复杂的错误处理逻辑

class ProcessingPipeline(BaseEngine):
    """
    最高级别的编排器
    """
    def __init__(self):
        self.parsing_engine = ParsingEngine()
        self.chunking_engine = ChunkingEngine()

    async def execute(
        self,
        policy: BasePolicy,
        file_url: str = None,
        file_content: Any = None,
        mime_type: str = None,
    ) -> List[DocumentChunk]:

        if file_content:
            # 优先使用直接提供的内容
            if isinstance(file_content, bytes):
                if not mime_type:
                    mime_type = get_mime_by_file_bytes(file_content)
            else:
                # 如果是文本，且没有指定解析器，直接创建文档
                if not policy.parser:
                    document = Document(
                        content=file_content,
                        content_type="list" if isinstance(file_content, list) else "text",
                        mime_type=mime_type or "text/plain",
                        source_parser="",
                        metadata={}
                    )
        elif file_url:
            # 下载内容
            file_content, mime_type = await get_file_bytes_and_mime(file_url)
        else:
            raise ValueError(f"file_content is empty")

        # --- 阶段一：解析 ---
        if policy.parser:
            document = await self.parsing_engine.execute(
                policy=policy.parser,
                file_content=file_content,
                mime_type=mime_type
            )

        if not document.content:
            return [DocumentChunk(content="", chunk_type="text", chunk_length=0, source_chunker="", metadata={})]

        # --- 阶段二：分块 ---
        if not policy.chunkers:
            # 如果没有分块策略，将整个文档内容作为一个块返回
            return [DocumentChunk(content=document.content, chunk_type="text", chunk_length=len(document.content), source_chunker="", metadata=document.metadata)]

        # 目前我们只支持并行处理，即所有分块器都作用于同一个原始 Document
        all_chunks = []
        tasks = [
            self.chunking_engine.execute(document, chunker_policy) 
            for chunker_policy in policy.chunkers
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                # 任何一步失败直接中止，后续再探索容错机制，但目前我们只需成功，不需失败
                raise ValueError(f"A chunker failed during execution: {result}")

            all_chunks.extend(result)

        return all_chunks