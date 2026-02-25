# src/app/engine/utils/tokenizer/manager.py

import logging
from typing import Dict, Type, Callable, Optional, Union
from .base import BaseTokenizer
from .bundled import TiktokenTokenizer, CharacterRatioTokenizer

logger = logging.getLogger(__name__)

# 定义构造函数类型：接收 model_name，返回实例
TokenizerFactory = Callable[[str], BaseTokenizer]

class TokenizerManager:
    """
    全局 Tokenizer 管理器。
    单例模式，负责路由和缓存。
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenizerManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._cache: Dict[str, BaseTokenizer] = {}
        self._factories: Dict[str, TokenizerFactory] = {}
        
        # --- 1. 注册默认策略 ---
        
        # OpenAI Family
        self.register_factory("openai", lambda m: TiktokenTokenizer(m))
        self.register_factory("azure", lambda m: TiktokenTokenizer(m))
        
        # DeepSeek / Qwen (通常兼容 cl100k_base)
        self.register_factory("deepseek", lambda m: TiktokenTokenizer("gpt-4"))
        self.register_factory("qwen", lambda m: TiktokenTokenizer("gpt-4"))
        
        # Google Gemini / Anthropic Claude
        # 如果没有安装特定库，使用保守估算 (1 token ≈ 2.5 chars for mixed content)
        self.register_factory("google", lambda m: CharacterRatioTokenizer(ratio=0.4))
        self.register_factory("anthropic", lambda m: CharacterRatioTokenizer(ratio=0.4))
        
        # Default Fallback
        self._default_factory = lambda m: TiktokenTokenizer("gpt-4")

    def register_factory(self, provider: str, factory: TokenizerFactory):
        """
        允许上层注册新的 Tokenizer 实现。
        provider: 厂商标识 (openai, google, zhipu...)
        """
        self._factories[provider.lower()] = factory

    def get_tokenizer(self, provider: str, model: str) -> BaseTokenizer:
        """
        获取 Tokenizer 实例。优先匹配 Provider，其次尝试推断。
        """
        cache_key = f"{provider}:{model}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        factory = None
        
        # 1. 尝试通过 Provider 路由
        if provider and provider.lower() in self._factories:
            factory = self._factories[provider.lower()]
        
        # 3. 兜底
        if not factory:
            logger.debug(f"No specific tokenizer found for {provider}/{model}, using default tiktoken.")
            factory = self._default_factory

        try:
            tokenizer = factory(model)
        except Exception as e:
            logger.error(f"Failed to instantiate tokenizer for {model}: {e}. Using CharRatio fallback.")
            tokenizer = CharacterRatioTokenizer()

        self._cache[cache_key] = tokenizer
        return tokenizer

    def count_tokens(self, text: str, provider: str, model: str) -> int:
        """便捷静态方法"""
        tokenizer = self.get_tokenizer(provider, model)
        return tokenizer.count(text)

# 全局单例
tokenizer_manager = TokenizerManager()