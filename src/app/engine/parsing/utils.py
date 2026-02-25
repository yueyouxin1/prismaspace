# engine/parsing/utils.py
import re
import httpx
import magic
from typing import Optional, Type, List, Dict, Any, Literal, NamedTuple, Union

magic_mime = magic.Magic(mime=True)

def get_mime_by_file_bytes(file_content: bytes) -> Optional[str]:
    return magic_mime.from_buffer(file_content)

async def get_file_bytes_and_mime(url: str) -> tuple[bytes, Optional[str]]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
        
        content_bytes = response.content
        mime_type = response.headers.get('content-type')
        
        if mime_type is None:
            mime_type = get_mime_by_file_bytes(content_bytes)
        
        return content_bytes, mime_type

def clean_text(text: str) -> str:
    # 中英文标点符号的Unicode范围
    chinese_punctuation = r'[\u3000-\u303F\uFF01-\uFF5E]'
    # 匹配连续的空白字符（包括空格、制表符、换页符等）和中英文标点
    pattern = f'〔\s+{chinese_punctuation}〕+'

    # 使用正则表达式替换匹配到的序列为空格
    cleaned_text = re.sub(pattern, ' ', text)
    # 再次替换多个连续空格为单个空格
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()

    return cleaned_text