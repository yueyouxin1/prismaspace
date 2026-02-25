# src/app/system/vector/constants.py

"""
[System Vector Constants]
定义系统级向量集合的命名规范和保留名称。
"""

# 1. 核心前缀定义
PREFIX_VECTOR_DB = "sys_vec_"      # 用于 Module 映射的集合
PREFIX_AGENT = "sys_agent_"        # 用于 Agent 系统功能的集合

# 前缀列表
MANAGED_PREFIXES = [PREFIX_VECTOR_DB, PREFIX_AGENT]

# 2. 预留集合定义
AGENT_LONG_TERM_CONTEXT_COLLECTION = f"{PREFIX_AGENT}long_term_context"

# [Strict] 预创建/保留集合列表
# 这些集合必须存在。如果在代码中移除了某项，下次启动时的 Pruning 逻辑会将其物理删除。
RESERVED_COLLECTIONS = [
    AGENT_LONG_TERM_CONTEXT_COLLECTION
]

DEFAULT_ENGINE_ALIAS = "default"