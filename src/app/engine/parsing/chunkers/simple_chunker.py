# engine/parsing/chunkers/simple_chunker.py
import re
from typing import Optional, Type, List, Dict, Any, Literal, NamedTuple, Union
from ..base import BaseChunker, Document, DocumentChunk, register_chunker
from ..utils import clean_text

@register_chunker
class SimpleChunker(BaseChunker):
    name = "simple_chunker_v1"

    async def run(self, document: Document, **kwargs) -> List[DocumentChunk]:
        if isinstance(document.content, list):
            # 未来应该由专门的ListChunker负责
            return [DocumentChunk(
                        content=item_content,
                        chunk_type="text",
                        chunk_length=len(item_content),
                        source_chunker=self.name,
                        metadata={}
                    ) for item_content in document.content] # 此策略只处理 list

        elif not isinstance(document.content, str) or document.content_type != "text":
            return [] # 此策略只处理 text

        chunk_size = kwargs.get('chunk_size', 600)

        document_chunks = []

        # 正则表达式，用于匹配句子结束的标点符号和换行符
        delimiter_pattern = re.compile(r'([?!。！？；;\n]+)')
        sentences = delimiter_pattern.split(document.content)

        current_paragraph = ''
        next_sentence_buffer = ''  # 用于存储无法完整加入当前段落的句子剩余部分

        for sentence in sentences:
            # 如果当前有未完成的句子内容，先添加到新句子开始
            if next_sentence_buffer:
                sentence = next_sentence_buffer + sentence
                next_sentence_buffer = ''  # 清空缓冲区

            sent_len = len(sentence)
            # 判断加上新句子后是否超过最大词数
            if len(current_paragraph) + sent_len <= chunk_size:
                current_paragraph += sentence
            else:
                # 查找句子中的分割符位置，确保在标点处截断
                last_delimiter_match = delimiter_pattern.search(sentence)
                if last_delimiter_match:
                    # 如果找到分割符，截取到分割符前并处理
                    split_pos = last_delimiter_match.start() + 1
                    current_paragraph += sentence[:split_pos].strip()
                    # 剩余部分留到下一次循环作为开头
                    next_sentence_buffer = sentence[split_pos:].strip()
                else:
                    # 如果没有找到合适的分割位置，整个句子移到下一个段落
                    if current_paragraph:  # 确保当前段落非空再添加
                        current_content = clean_text(current_paragraph.strip())
                        document_chunks.append(
                            DocumentChunk(
                                content=current_content,
                                chunk_type="text",
                                chunk_length=len(current_content),
                                source_chunker=self.name,
                                metadata={}
                            )
                        )
                    current_paragraph = sentence
                    next_sentence_buffer = ''  # 无需缓冲，因为整个句子已加入新段落

        # 处理最后一个段落或剩余内容
        if current_paragraph or next_sentence_buffer:
            last_content = clean_text((current_paragraph + next_sentence_buffer).strip())
            document_chunks.append(
                DocumentChunk(
                    content=last_content,
                    chunk_type="text",
                    chunk_length=len(last_content),
                    source_chunker=self.name,
                    metadata={}
                )
            )

        return document_chunks