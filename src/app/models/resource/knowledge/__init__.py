# src/app/models/resource/knowledge/__init__.py

# 1. 导入并导出这个子域的所有公开模型
from .knowledge_base import KnowledgeBase, KnowledgeBaseVersionDocuments
from .knowledge_document import KnowledgeDocument, DocumentProcessingStatus
from .knowledge_chunk import KnowledgeChunk, ChunkProcessingStatus

# 2. 导入注册中心
from ..base import ALL_INSTANCE_TYPES

# 3. 将自己注册进去
ALL_INSTANCE_TYPES.append(KnowledgeBase)