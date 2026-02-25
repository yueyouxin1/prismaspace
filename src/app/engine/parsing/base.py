# engine/parsing/base.py
from abc import ABC, abstractmethod
from typing import Optional, Type, List, Dict, Any, Literal, NamedTuple, Union

# --- 1. 定义标准化的输出结构 (The "Chunk" Contract) ---
# 这是所有解析器必须返回的统一格式
class Document(NamedTuple):
    content: Union[str, list]
    content_type: Literal["text", "list", "html", "xml"] # 可扩展
    mime_type: str
    source_parser: str
    metadata: Dict[str, Any]

# 这是所有分段器必须返回的统一格式
class DocumentChunk(NamedTuple):
    content: str
    chunk_type: Literal["text", "qa"] # 可扩展
    chunk_length: int
    source_chunker: str
    metadata: Dict[str, Any] # e.g., length, page_number, source_file, bounding_box

# --- 2. 定义解析策略的输入结构 (The "Policy" Contract) ---
# 这是调用者告诉引擎“如何解析”的指令
class ParserPolicy(NamedTuple):
    parser_name: str # e.g., 'simple_text', 'gpt4v_ocr'
    allowed_mime_types: list
    params: Dict[str, Any] = {} # 特定于解析器的参数

class ChunkerPolicy(NamedTuple):
    chunker_name: str # e.g., simple_text_v1, simple_qa_v1
    params: Dict[str, Any] = {} # 特定于解析器的参数

class BasePolicy(NamedTuple):
    parser: Optional[ParserPolicy] = None
    chunkers: Optional[List[ChunkerPolicy]] = []

# --- 定义主引擎的接口 (The "Engine" Contract) ---
class BaseEngine(ABC):
    """文档解析引擎的统一门面。"""

    @abstractmethod
    async def execute(
        self,
        file_url: Optional[str],
        file_content: Optional[bytes],
        mime_type: Optional[str],
        policy: BasePolicy
    ) -> List[DocumentChunk]:
        """
        引擎的主入口点。
        它接收文件内容和解析策略，并调度正确的解析器来完成工作。
        """
        raise NotImplementedError

# --- 定义具体解析器的接口 (The "Parser" Contract) ---
class BaseParser(ABC):
    """所有具体解析器（Tika, Whisper, VLM等）都必须实现的接口。"""

    # 静态属性，用于在引擎中注册和查找
    name: str = "base_parser"

    support_mime_types: list = []

    @abstractmethod
    async def run(self, file_content: bytes, mime_type: str, **kwargs) -> Document:
        """
        接收原始文件内容，返回标准化的 DocumentChunk 列表。
        **kwargs 用于接收来自 ParsingPolicy.params 的特定参数。
        """
        raise NotImplementedError

class BaseChunker(ABC):
    # 策略的唯一名称，用于在数据库和配置中引用
    name: str 

    @abstractmethod
    async def run(
        self, 
        document: Document, 
        **kwargs # 允许传入特定于策略的参数，如 chunk_size
    ) -> List[DocumentChunk]:
        """
        接收原始文件字节流，返回标准化的 DocumentChunk 列表。
        所有的解析、结构化、分割逻辑都封装在此方法内部。
        """
        raise NotImplementedError

ALL_PARSERS: Dict[str, Type[BaseParser]] = {}
ALL_CHUNKERS: Dict[str, Type[BaseChunker]] = {}
ALL_MIME_TYPE: List[str] = []

def register_parser(cls: Type[BaseParser]):
    if cls.name in ALL_PARSERS:
        raise ValueError(f"Parser with name '{cls.name}' already registered.")
    ALL_MIME_TYPE.extend(mime for mime in cls.support_mime_types 
                        if mime not in ALL_MIME_TYPE)
    ALL_PARSERS[cls.name] = cls
    return cls

def register_chunker(cls: Type[BaseChunker]):
    if cls.name in ALL_CHUNKERS:
        raise ValueError(f"Chunker with name '{cls.name}' already registered.")
    ALL_CHUNKERS[cls.name] = cls
    return cls