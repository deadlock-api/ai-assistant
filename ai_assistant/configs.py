import logging
import os

from smolagents import LiteLLMModel, InferenceClientModel

from ai_assistant.message_store import MessageStore, RedisMessageStore, MemoryMessageStore
from ai_assistant.utils import format_table_schema, list_clickhouse_tables

LOGGER = logging.getLogger(__name__)

TABLES_CONTEXT = "\n\n".join(format_table_schema(table) for table in list_clickhouse_tables())
AGENT_INSTRUCTIONS = f"Available Clickhouse Tables:\n{TABLES_CONTEXT}"

MODEL_CONFIGS = {
    "gemini-flash": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-flash"),
    "gemini-pro": lambda: LiteLLMModel(model_id="gemini/gemini-2.5-pro"),
    "ollama": lambda: LiteLLMModel(model_id="ollama/qwen2.5-coder:14b"),
    "hf": lambda: InferenceClientModel(),
}
DEFAULT_MODEL = "ollama"


def get_default_model():
    return MODEL_CONFIGS[DEFAULT_MODEL]()


def get_message_store() -> MessageStore:
    if "REDIS_HOST" in os.environ:
        LOGGER.info("Using Redis Message Store")
        return RedisMessageStore()
    else:
        LOGGER.info("Using In-Memory Message Store")
        return MemoryMessageStore()
