# src/app/services/resource/agent/prompt_template.py

import re
import logging
from typing import Dict, Any, Optional
from app.core.context import AppContext

logger = logging.getLogger(__name__)

class PromptTemplate:
    """
    负责解析 Agent 系统提示词模板，注入记忆变量和工具引用。
    支持语法: {#LibraryBlock type=memory_key id=my_var#}Default Value{#/LibraryBlock#}
    """
    PATTERN = re.compile(
        r'{#LibraryBlock\s+([^#]+?)#}(.*?){#/LibraryBlock#}',  # 匹配特殊块
        re.DOTALL  # 允许.匹配换行符
    )

    def render(
        self, 
        raw_prompt: str,
        memory_obj: Optional[dict] = None
    ) -> str:
        if not memory_obj:
            memory_obj = {}

        def parse_attributes(attrs_str:str):
            """解析属性字符串为字典"""
            attrs = {}
            for pair in attrs_str.strip().split():
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    attrs[key] = value.strip('"').strip("'")
            return attrs

        def process_library_skill(attrs:dict, content:str):
            library_type = attrs.get('type')
            library_id = attrs.get('id', "")
            return f"{library_id}"

        def process_library_memory(
            attrs:dict, 
            content:str
        ):
            memory_key = attrs.get('id')
            # 调用 MemoryService 获取运行时值
            new_content = memory_obj.get(memory_key, "") or ""
            return new_content

        def process_library_block(attrs:dict, content:str):
            library_type = attrs.get('type')
            if library_type == 'memory_key':
                return process_library_memory(attrs, content)
            return process_library_skill(attrs, content)

        def process_replacer(match):
            attrs_str = match.group(1)
            content = match.group(2).strip()
            attrs = parse_attributes(attrs_str)
            new_content = process_library_block(attrs, content)
            return new_content  # 返回替换后的内容

        return self.PATTERN.sub(process_replacer, raw_prompt)