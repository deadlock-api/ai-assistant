import os
import pytest
from smolagents import AgentMemory

from ai_assistant.message_store import MemoryMessageStore, RedisMessageStore


def test_memory_message_store():
    store = MemoryMessageStore()
    prev_memory = AgentMemory("test")
    memory_id = store.save_memory(prev_memory)
    retrieved_memory = store.get_memory(memory_id)
    assert retrieved_memory.system_prompt == prev_memory.system_prompt
    assert retrieved_memory.steps == prev_memory.steps


@pytest.mark.skipif("REDIS_HOST" not in os.environ, reason="Redis not available")
def test_redis_message_store():
    store = RedisMessageStore()
    prev_memory = AgentMemory("test")
    memory_id = store.save_memory(prev_memory)
    retrieved_memory = store.get_memory(memory_id)
    assert retrieved_memory.system_prompt == prev_memory.system_prompt
    assert retrieved_memory.steps == prev_memory.steps
