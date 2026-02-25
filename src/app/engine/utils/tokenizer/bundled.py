# src/app/engine/utils/tokenizer/bundled.py

import logging
from typing import List, Optional
try:
    import tiktoken
except ImportError:
    tiktoken = None

from .base import BaseTokenizer

logger = logging.getLogger(__name__)

class TiktokenTokenizer:
    """
    基于 OpenAI tiktoken 的实现。
    支持 model 自动推断 encoding。
    """
    def __init__(self, model_name: str = "gpt-4"):
        if not tiktoken:
            raise ImportError("tiktoken is not installed.")
        
        try:
            self.encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            # 对于未知模型，默认使用 GPT-4 的 cl100k_base，这是目前最通用的 BPE
            logger.warning(f"Model '{model_name}' not found in tiktoken. Falling back to 'cl100k_base'.")
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def encode(self, text: str) -> List[int]:
        return self.encoding.encode(text)

    def decode(self, tokens: List[int]) -> str:
        return self.encoding.decode(tokens)

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self.encode(text))

class CharacterRatioTokenizer:
    """
    [兜底策略] 基于字符比率的估算器。
    用于没有本地 Tokenizer 库的模型 (如 Gemini, Claude 的本地库比较重或不存在)。
    """
    def __init__(self, ratio: float = 0.3):
        # 经验值：中文约 0.6-0.8，英文约 0.25-0.3。
        # 为了计费安全（宁少勿多防止超扣），或者为了上下文安全（宁多勿少防止超长），策略不同。
        # 这里取一个折中值 0.5 (2 chars ≈ 1 token)
        self.ratio = ratio

    def encode(self, text: str) -> List[int]:
        # 伪实现：返回虚构的 token ids
        return [0] * self.count(text)

    def decode(self, tokens: List[int]) -> str:
        raise NotImplementedError("Ratio tokenizer cannot decode.")

    def count(self, text: str) -> int:
        if not text:
            return 0
        return int(len(text) * self.ratio) + 1