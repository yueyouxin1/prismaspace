# src/app/engine/parsing/chunkers/context_chunker.py

import logging
from typing import List
import tiktoken
from ..base import BaseChunker, Document, DocumentChunk, register_chunker
from ..utils import clean_text

logger = logging.getLogger(__name__)

@register_chunker
class ContextChunker(BaseChunker):
    """
    [Deep Memory] 专门用于切分对话上下文的 Chunker。
    它不仅基于字符长度，还基于 Token 数量进行安全切分，确保符合 Embedding 模型的限制。
    """
    name = "context_chunker_v1"

    def __init__(self):
        # 默认使用 cl100k_base (GPT-4/3.5/Embedding-v3 标准)
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Fallback if download fails (rare in prod, but safe)
            self.tokenizer = None

    def _count_tokens(self, text: str) -> int:
        if not self.tokenizer:
            return len(text)
        return len(self.tokenizer.encode(text))

    def _split_text_with_overlap(self, text: str, max_tokens: int, overlap_tokens: int) -> List[str]:
        """
        基于 Token 的重叠切分算法。
        """
        if self._count_tokens(text) <= max_tokens:
            return [text]
        
        chunks = []
        tokens = self.tokenizer.encode(text)
        total_tokens = len(tokens)
        start = 0
        
        while start < total_tokens:
            end = min(start + max_tokens, total_tokens)
            chunk_tokens = tokens[start:end]
            chunk_text = self.tokenizer.decode(chunk_tokens)
            chunks.append(chunk_text)
            
            if end == total_tokens:
                break
                
            start += (max_tokens - overlap_tokens)
            
        return chunks

    async def run(self, document: Document, **kwargs) -> List[DocumentChunk]:
        """
        Args:
            document: 包含整轮对话文本的 Document 对象
            kwargs:
                max_tokens: 每个 Chunk 的最大 Token 数 (default: 512)
                overlap_tokens: 重叠 Token 数 (default: 50)
        """
        content = document.content
        if not isinstance(content, str) or not content.strip():
            return []

        max_tokens = kwargs.get('max_tokens', 512)
        overlap_tokens = kwargs.get('overlap_tokens', 50)

        # 清理文本，移除无意义的空白符
        cleaned_text = clean_text(content)
        
        # 执行切分
        text_chunks = self._split_text_with_overlap(cleaned_text, max_tokens, overlap_tokens)
        
        doc_chunks = []
        for i, text in enumerate(text_chunks):
            doc_chunks.append(DocumentChunk(
                content=text,
                chunk_type="text",
                chunk_length=len(text),
                source_chunker=self.name,
                metadata={
                    "chunk_index": i,
                    "total_chunks": len(text_chunks)
                }
            ))
            
        return doc_chunks