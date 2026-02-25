# src/app/engine/utils/tokenizer/base.py

from typing import List, Protocol, Union

class BaseTokenizer(Protocol):
    """
    Tokenizer 的统一协议接口。
    """
    def encode(self, text: str) -> List[int]:
        """将文本转换为 Token ID 列表"""
        ...

    def decode(self, tokens: List[int]) -> str:
        """将 Token ID 列表转换回文本"""
        ...

    def count(self, text: str) -> int:
        """快速计算文本的 Token 数量"""
        ...