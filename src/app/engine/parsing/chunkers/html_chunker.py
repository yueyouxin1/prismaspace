# engine/parsing/chunkers/xml_chunker.py
from typing import Optional, Type, List, Dict, Any, Literal, NamedTuple, Union
from bs4 import BeautifulSoup
from ..base import BaseChunker, Document, DocumentChunk, register_chunker
from ..utils import clean_text

@register_chunker
class HtmlChunker(BaseChunker):
    name = "html_chunker_v1"

    async def chunk_pdf(self, document: Document) -> List[DocumentChunk]:
        # 使用BeautifulSoup解析XHTML内容
        soup = BeautifulSoup(document.content, 'lxml')

        # 查找所有class为"page"的div元素
        pages = soup.find_all('div', class_='page')

        # 提取每一页的内容
        document_chunks = []
        for i, page in enumerate(pages):
            page_text = page.get_text(strip=True)
            
            clean_content = clean_text(page_text)
            document_chunks.append(
                DocumentChunk(
                    content=clean_content,
                    chunk_type="text",
                    chunk_length=len(clean_content),
                    source_chunker=self.name,
                    metadata={"page_number": i + 1}
                )
            )

        return document_chunks 

    async def run(self, document: Document, **kwargs) -> List[DocumentChunk]:
        if not isinstance(document.content, str) or document.content_type != "xml":
            return [] # 此策略只处理 XML
        mime_type = document.mime_type
        document_chunks = []
        # 暂时只处理PDF文件，可扩展
        if mime_type == "application/pdf":
            document_chunks = await self.chunk_pdf(document)

        return document_chunks