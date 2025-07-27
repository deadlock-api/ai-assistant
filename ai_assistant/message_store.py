import json
import logging
import os
import uuid
from abc import ABC, abstractmethod
from typing import ClassVar
from uuid import UUID

import redis
from smolagents import ChatMessage, AgentMemory

LOGGER = logging.getLogger(__name__)


class MessageStore(ABC):
    def save_memory(self, memory: AgentMemory) -> UUID:
        LOGGER.debug("Saving memory")
        memory_id = self._save_memory([m for step in memory.steps for m in step.to_messages()])
        LOGGER.info(f"Saved memory with ID {memory_id}")
        return memory_id

    def get_memory(self, memory_id: UUID) -> list[ChatMessage] | None:
        LOGGER.debug(f"Retrieving memory with ID {memory_id}")
        memory = self._get_memory(memory_id)
        if memory is None:
            LOGGER.error(f"Failed to retrieve memory with ID {memory_id}")
            return None
        LOGGER.info(f"Retrieved memory with ID {memory_id}")
        return memory

    @abstractmethod
    def _save_memory(self, memory: list[ChatMessage]) -> UUID:
        raise NotImplementedError

    @abstractmethod
    def _get_memory(self, memory_id: UUID) -> list[ChatMessage]:
        raise NotImplementedError


class MemoryMessageStore(MessageStore):
    memory: dict[UUID, list[ChatMessage]] = {}

    def _save_memory(self, memory: list[ChatMessage]) -> UUID:
        memory_id = uuid.uuid4()
        self.memory[memory_id] = memory
        return memory_id

    def _get_memory(self, memory_id: UUID) -> list[ChatMessage] | None:
        return self.memory.get(memory_id)


class RedisMessageStore(MessageStore):
    conn: redis.Redis
    expire: int

    HOST: ClassVar[str] = os.environ.get("REDIS_HOST", "localhost")
    PORT: ClassVar[int] = os.environ.get("REDIS_PORT", 6379)
    PASS: ClassVar[str | None] = os.environ.get("REDIS_PASSWORD")

    def __init__(self, expire: int = 60 * 60):
        self.conn = redis.Redis(host=self.HOST, port=self.PORT, password=self.PASS)
        self.expire = expire

    def _save_memory(self, memory: list[ChatMessage]) -> UUID:
        memory_id = uuid.uuid4()
        data = json.dumps([m.dict() for m in memory])
        self.conn.set(str(memory_id), data, ex=self.expire)
        return memory_id

    def _get_memory(self, memory_id: UUID) -> list[ChatMessage] | None:
        try:
            result = json.loads(self.conn.get(str(memory_id)))
            return [ChatMessage.from_dict(m) for m in result]
        except TypeError as e:
            import traceback

            traceback.print_exc()
            LOGGER.error(f"Failed to retrieve memory with ID {memory_id}: {e}")
            return None
