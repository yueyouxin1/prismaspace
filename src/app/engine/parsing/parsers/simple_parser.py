# engine/parsing/parsers/simple_parser.py
import httpx 
from ..base import BaseParser, Document, register_parser

@register_parser
class SimpleParser(BaseParser):

    name: str = "simple_parser_v1"

    support_mime_types: list = [
        # 常见文档类型
        "application/pdf",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/rtf",
        "text/plain",
        "text/csv",
        "text/html",
        "​​text/markdown",
        "application/xhtml+xml",
        # 常见编程相关
        "text/x-java-source",
        "text/x-c++src",
        "application/java-archive",
        "application/xml",
        # 其他常见类型
        "application/json",
        "application/javascript",
        "application/octet-stream",
    ]

    async def run(self, file_content: bytes, mime_type: str, **kwargs) -> Document:
        if not isinstance(file_content, bytes):
            raise ValueError("File content type not bytes")

        tika_url = kwargs.get('tika_url')

        if not tika_url:
            raise ValueError("Tika Service Url is empty")

        is_xml = False

        is_pdf = mime_type == "application/pdf"

        if is_pdf:
            # 如果是pdf，则返回可结构化解析的XML内容
            is_xml = True

        accept_header = "text/html" if is_xml else "text/plain"

        content_type = "xml" if is_xml else "text"

        content = ""

        async with httpx.AsyncClient(timeout=30) as client:

            # 调用 tika 的解析端点
            try:
                response = await client.put(
                    tika_url,
                    headers={
                        "Accept": accept_header,
                        "X-Tika-OCRLanguage": "chi_sim",
                        "X-Tika-PDFextractInlineImages": "true"
                    },
                    content=file_content
                )
                response.raise_for_status()  # 检查 HTTP 状态码
                content = response.text
            except httpx.RequestError as e:
                raise ValueError(f"Tika request failed: {str(e)}")    
        
        if not content:
            return Document(content="", content_type=content_type, mime_type=mime_type, metadata={})

        return Document(
                    content=content,
                    content_type=content_type,
                    mime_type=mime_type,
                    source_parser=self.name,
                    metadata={}
                )    