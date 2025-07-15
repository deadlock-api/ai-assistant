import logging
import os
import pickle
import uuid
from abc import ABC, abstractmethod
from typing import ClassVar
from uuid import UUID

import redis
from smolagents import AgentMemory

LOGGER = logging.getLogger(__name__)


class MessageStore(ABC):
    def save_memory(self, memory: AgentMemory) -> UUID:
        LOGGER.debug("Saving memory")
        memory_id = self._save_memory(memory)
        LOGGER.info(f"Saved memory with ID {memory_id}")
        return memory_id

    def get_memory(self, memory_id: UUID) -> AgentMemory:
        LOGGER.debug(f"Retrieving memory with ID {memory_id}")
        memory = self._get_memory(memory_id)
        LOGGER.info(f"Retrieved memory with ID {memory_id}")
        return memory

    @abstractmethod
    def _save_memory(self, memory: AgentMemory) -> UUID:
        raise NotImplementedError

    @abstractmethod
    def _get_memory(self, memory_id: UUID) -> AgentMemory:
        raise NotImplementedError


class MemoryMessageStore(MessageStore):
    memory: dict[UUID, AgentMemory] = {}

    def _save_memory(self, memory: AgentMemory) -> UUID:
        memory_id = uuid.uuid4()
        self.memory[memory_id] = memory
        return memory_id

    def _get_memory(self, memory_id: UUID) -> AgentMemory:
        return self.memory[memory_id]


class RedisMessageStore(MessageStore):
    conn: redis.Redis
    expire: int

    HOST: ClassVar[str] = os.environ.get("REDIS_HOST", "localhost")
    PORT: ClassVar[int] = os.environ.get("REDIS_PORT", 6379)

    def __init__(self, expire: int = 60 * 60):
        self.conn = redis.Redis(host=self.HOST, port=self.PORT)
        self.expire = expire

    def _save_memory(self, memory: AgentMemory) -> UUID:
        memory_id = uuid.uuid4()
        self.conn.set(str(memory_id), pickle.dumps(memory), ex=self.expire)
        return memory_id

    def _get_memory(self, memory_id: UUID) -> AgentMemory:
        return pickle.loads(self.conn.get(str(memory_id)))
