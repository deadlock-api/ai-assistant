# Deadlock AI Assistant - Chat Endpoint

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from packages.ai_assistant.agent import (
    AgentAuthError,
    AgentConfigurationError,
    AgentError,
    AgentRateLimitError,
    AgentRetryExhaustedError,
    AgentTimeoutError,
    DeadlockAgentClient,
    _build_prompt_with_history,
    get_agent_config,
)
from packages.api.models import (
    ChatDeltaEvent,
    ChatEndEvent,
    ChatErrorEvent,
    ChatStartEvent,
    ChatToolEndEvent,
    ChatToolStartEvent,
    ChatUsageEvent,
    serialize_sse_event,
)
from packages.integrations.conversation import (
    ConversationNotFoundError,
    add_message,
    generate_conversation_id,
    get_conversation_history,
)
from packages.integrations.redis_client import RedisUnavailableError
from packages.integrations.sse_cache import (
    cache_sse_stream,
    generate_cache_key,
    get_cached_sse_stream,
    replay_cached_stream,
)
from packages.tools.registry import ToolRegistry

logger = logging.getLogger("deadlock_assistant")

router = APIRouter()

# Maximum number of retries when the agent returns empty content
MAX_EMPTY_RESPONSE_RETRIES = 2


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    message: str
    conversation_id: str | None = None


@dataclass
class _StreamResult:
    """Mutable container to capture the accumulated response content from a stream."""

    content: str = ""


def _drain_tool_events(
    tool_event_queue: asyncio.Queue[ChatToolStartEvent | ChatToolEndEvent | ChatUsageEvent | None],
) -> list[str]:
    """Drain pending tool/usage events from the queue and return them as serialized SSE strings."""
    events: list[str] = []
    while not tool_event_queue.empty():
        try:
            tool_event = tool_event_queue.get_nowait()
            if tool_event is not None:
                events.append(serialize_sse_event(tool_event))
        except asyncio.QueueEmpty:
            break
    return events


async def _stream_client_response(
    client: DeadlockAgentClient,
    message: str,
    tool_event_queue: asyncio.Queue[ChatToolStartEvent | ChatToolEndEvent | ChatUsageEvent | None],
    result: _StreamResult,
) -> AsyncIterator[str]:
    """Send a message and yield SSE events as they arrive (truly streaming).

    Tool start/end events and text deltas are yielded immediately so the
    client sees progress in real time.

    Args:
        client: The connected DeadlockAgentClient.
        message: The message to send.
        tool_event_queue: Queue for tool/usage events from SSE callback.
        result: Mutable container where accumulated text content is stored.

    Yields:
        Serialized SSE event strings.
    """
    async for chunk in client.send_message(message):
        # Yield any tool events that arrived while waiting for this chunk
        for event_str in _drain_tool_events(tool_event_queue):
            yield event_str

        if chunk.content:
            result.content += chunk.content
            yield serialize_sse_event(ChatDeltaEvent(content=chunk.content))

    # Drain remaining tool events after the stream ends
    for event_str in _drain_tool_events(tool_event_queue):
        yield event_str


NUDGE_MESSAGE = (
    "You used tools but did not provide a response to the user. "
    "Please summarize the tool results and answer the user's question now."
)


async def _generate_sse_stream(
    message: str,
    conversation_id: str,
    history: list[dict[str, str]],
    event_collector: list[str] | None = None,
) -> AsyncIterator[str]:
    """Generate an SSE stream for a chat request.

    Uses a single DeadlockAgentClient session so that when the model returns
    empty content after tool use (common with free-tier models), a follow-up
    nudge message can be sent within the same session — preserving tool result
    context instead of retrying from scratch.

    Args:
        message: The user message.
        conversation_id: The conversation ID.
        history: The conversation history.
        event_collector: Optional list to collect events for caching.

    Yields:
        SSE formatted event strings.
    """

    # Helper to yield and optionally collect events for caching
    def collect(event_str: str) -> None:
        if event_collector is not None:
            event_collector.append(event_str)

    # Send start event
    start_event = serialize_sse_event(ChatStartEvent(conversation_id=conversation_id))
    collect(start_event)
    yield start_event

    tool_event_queue: asyncio.Queue[ChatToolStartEvent | ChatToolEndEvent | ChatUsageEvent | None] = asyncio.Queue()

    response_content = ""

    # Create SSE callback that adds tool/usage events to the queue
    def sse_callback(event: ChatToolStartEvent | ChatToolEndEvent | ChatUsageEvent) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            tool_event_queue.put_nowait(event)

    tool_registry = ToolRegistry(sse_callback=sse_callback)
    config = get_agent_config()
    client = DeadlockAgentClient(config=config, tool_registry=tool_registry, sse_callback=sse_callback)

    try:
        await client.connect()

        # Build full prompt with history (same as stream_response does)
        full_prompt = _build_prompt_with_history(message, history)

        # First attempt: send the actual user message, streaming events in real time
        result = _StreamResult()
        async for event_str in _stream_client_response(client, full_prompt, tool_event_queue, result):
            collect(event_str)
            yield event_str

        response_content = result.content

        # If we got content on the first try, we're done
        if not response_content.strip():
            # Empty response after tool use — send nudge messages within the same session
            for attempt in range(1, MAX_EMPTY_RESPONSE_RETRIES + 1):
                logger.warning(
                    "Agent returned empty response, sending nudge",
                    extra={
                        "conversation_id": conversation_id,
                        "attempt": attempt,
                        "max_retries": MAX_EMPTY_RESPONSE_RETRIES,
                    },
                )
                # Emit a delta so the user knows we're retrying
                retry_event = serialize_sse_event(ChatDeltaEvent(content="\n\n*Retrying request...*\n\n"))
                collect(retry_event)
                yield retry_event

                # Send nudge within the SAME session — model still has tool results in context
                result = _StreamResult()
                async for event_str in _stream_client_response(client, NUDGE_MESSAGE, tool_event_queue, result):
                    collect(event_str)
                    yield event_str

                response_content = result.content
                if response_content.strip():
                    break
            else:
                # All nudge attempts exhausted
                logger.warning(
                    "Agent returned empty response after all nudge attempts",
                    extra={
                        "conversation_id": conversation_id,
                        "attempts": MAX_EMPTY_RESPONSE_RETRIES,
                        "user_message": message[:200],
                    },
                )
                yield serialize_sse_event(
                    ChatErrorEvent(
                        error="The assistant was unable to generate a response. Please try again.",
                        code="EMPTY_RESPONSE",
                    )
                )
                return

        # Save user message and assistant response to history
        await add_message(conversation_id, "user", message)
        await add_message(conversation_id, "assistant", response_content)

        # Send end event
        end_event = serialize_sse_event(ChatEndEvent(conversation_id=conversation_id))
        collect(end_event)
        yield end_event

    except AgentConfigurationError as e:
        logger.error("Agent config error", extra={"conversation_id": conversation_id, "error": str(e)})
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_CONFIGURATION_ERROR"))
    except AgentTimeoutError as e:
        logger.error("Agent timeout during chat", extra={"conversation_id": conversation_id, "error": str(e)})
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_TIMEOUT"))
    except AgentAuthError as e:
        logger.error("Agent auth error during chat", extra={"conversation_id": conversation_id, "error": str(e)})
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_AUTH_ERROR"))
    except AgentRateLimitError as e:
        logger.warning("Agent rate limited during chat", extra={"conversation_id": conversation_id, "error": str(e)})
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_RATE_LIMIT"))
    except AgentRetryExhaustedError as e:
        logger.error("Agent retries exhausted during chat", extra={"conversation_id": conversation_id, "error": str(e)})
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_RETRY_EXHAUSTED"))
    except AgentError as e:
        logger.error("Agent error during chat", extra={"conversation_id": conversation_id, "error": str(e)})
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_ERROR"))
    except RedisUnavailableError:
        logger.error("Redis unavailable during conversation save", extra={"conversation_id": conversation_id})
        yield serialize_sse_event(ChatErrorEvent(error="Failed to save conversation history", code="REDIS_ERROR"))
    finally:
        await client.disconnect()


async def _cached_sse_stream(
    message: str,
    conversation_id: str,
    history: list[dict[str, str]],
    cache_key: str,
) -> AsyncIterator[str]:
    """Generate SSE stream with caching support.

    Wraps _generate_sse_stream to collect events and cache them on success.

    Args:
        message: The user message.
        conversation_id: The conversation ID.
        history: The conversation history.
        cache_key: The cache key to store events under.

    Yields:
        SSE formatted event strings.
    """
    event_collector: list[str] = []
    success = False

    async for event in _generate_sse_stream(message, conversation_id, history, event_collector):
        yield event
        # Check if this was the end event (successful completion)
        if '"event":"end"' in event:
            success = True

    # Cache only on successful completion (cache failure is non-fatal)
    if success and event_collector:
        try:  # noqa: SIM105 - contextlib.suppress doesn't work with async
            await cache_sse_stream(cache_key, event_collector)
        except RedisUnavailableError:
            pass


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Process a chat message and stream the AI response via SSE.

    If the exact same prompt (message + history) has been seen before,
    the cached SSE stream is replayed instead of generating a new response.

    Args:
        request: The chat request containing message and optional conversation_id.

    Returns:
        SSE stream with chat events.
    """
    logger.info("Chat request received", extra={"conversation_id": request.conversation_id or "new"})

    # Get or create conversation
    if request.conversation_id:
        conversation_id = request.conversation_id
        try:
            history_messages = await get_conversation_history(conversation_id)
            history = [{"role": msg.role, "content": msg.content} for msg in history_messages]
        except ConversationNotFoundError:
            # Conversation doesn't exist, start fresh
            history = []
        except RedisUnavailableError:
            # Redis unavailable, return error stream
            async def error_stream() -> AsyncIterator[str]:
                yield serialize_sse_event(
                    ChatErrorEvent(error="Failed to load conversation history", code="REDIS_ERROR")
                )

            return StreamingResponse(
                error_stream(),
                media_type="text/event-stream",
            )
    else:
        conversation_id = generate_conversation_id()
        history = []

    # Generate cache key from message and history
    cache_key = generate_cache_key(request.message, history)

    # Check for cached response
    try:
        cached_events = await get_cached_sse_stream(cache_key)
        if cached_events is not None:
            logger.info("SSE cache hit", extra={"conversation_id": conversation_id, "cache_key": cache_key[:32]})
            return StreamingResponse(
                replay_cached_stream(cached_events),
                media_type="text/event-stream",
            )
    except RedisUnavailableError:
        logger.warning("SSE cache lookup failed: Redis unavailable", extra={"conversation_id": conversation_id})
        # Cache lookup failed, proceed without cache
        pass

    return StreamingResponse(
        _cached_sse_stream(request.message, conversation_id, history, cache_key),
        media_type="text/event-stream",
    )
