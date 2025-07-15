import logging
import os

from smolagents import LiteLLMModel, InferenceClientModel, ApiModel

from ai_assistant.message_store import MessageStore, RedisMessageStore, MemoryMessageStore
from ai_assistant.utils import format_table_schema, list_clickhouse_tables

LOGGER = logging.getLogger(__name__)

TABLES_CONTEXT = "\n\n".join(format_table_schema(table) for table in list_clickhouse_tables())
AGENT_INSTRUCTIONS = f"Available Clickhouse Tables:\n{TABLES_CONTEXT}"

MODEL_CONFIGS = {
    "gemini-flash-lite": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-flash-lite"),
    "gemini-flash": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-flash"),
    "gemini-pro": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-pro"),
    "ollama": lambda: LiteLLMModel(model_id="ollama/qwen2.5-coder:14b"),
    "hf": lambda: InferenceClientModel(),
}


def get_model() -> ApiModel:
    if model := os.environ.get("MODEL"):
        if model in MODEL_CONFIGS:
            return MODEL_CONFIGS[model]()

    if "GEMINI_API_KEY" in os.environ:
        LOGGER.info("Using Google Gemini Flash Model")
        return MODEL_CONFIGS["gemini-flash"]()
    elif "HF_TOKEN" in os.environ:
        LOGGER.info("Using Hugging Face Inference API")
        return MODEL_CONFIGS["hf"]()
    else:
        raise ValueError(f"Invalid model: {model}")


def get_message_store() -> MessageStore:
    if "REDIS_HOST" in os.environ:
        LOGGER.info("Using Redis Message Store")
        return RedisMessageStore()
    else:
        LOGGER.info("Using In-Memory Message Store")
        return MemoryMessageStore()
