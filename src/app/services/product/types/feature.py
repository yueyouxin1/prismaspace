import enum

class FeatureRole(str, enum.Enum):
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    EMBEDDING_TOKEN = "embedding_token"
    IMAGE_GENERATION_CALL = "image_generation_call"
    # ... 可以无限扩展