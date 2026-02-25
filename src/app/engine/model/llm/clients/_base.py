# src/app/engine/model/llm/clients/_base.py

from typing import List, Dict, Any
from ..base import LLMMessage, LLMTool, LLMUsage
from ....utils.tokenizer.manager import tokenizer_manager

class LLMClientBase:
    """提供一些具体客户端可以复用的辅助方法。"""
    
    def _messages_to_dict(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        """将Pydantic模型转换为符合OpenAI API格式的字典列表。"""
        results = []
        for msg in messages:
            # Pydantic的 model_dump 方法可以很好地处理这个问题，并排除None值
            results.append(msg.model_dump(exclude_none=True))
        return results

    def _tools_to_dict(self, tools: List[LLMTool]) -> List[Dict[str, Any]]:
        """将工具列表转换为字典列表。"""
        if not tools:
            return None
        return [tool.model_dump() for tool in tools]

    def _estimate_usage(
        self, 
        provider: str, 
        model: str, 
        messages: List[LLMMessage], 
        generated_content: str = ""
    ) -> LLMUsage:
        """
        [兜底策略] 当 API 未返回 usage 时，使用本地 tokenizer 进行估算。
        """
        tokenizer = tokenizer_manager.get_tokenizer(provider, model)
        # 1. 计算 Prompt Tokens
        # 将消息列表还原为文本 (这里做一个简单的拼接，生产环境可以使用更精确的 chat format 估算)
        prompt_text = ""
        for msg in messages:
            content = msg.content or ""
            if msg.tool_calls:
                content += str(msg.tool_calls)
            prompt_text += content

        # 2. 调用 Tokenizer
        prompt_tokens = tokenizer.count(prompt_text)
        completion_tokens = tokenizer.count(generated_content)

        # 3. 累加到总用量
        return LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens
        )