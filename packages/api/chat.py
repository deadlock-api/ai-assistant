# Deadlock AI Assistant - Chat Endpoint

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

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
    stream_response,
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


async def _stream_agent_response(
    message: str,
    history: list[dict[str, str]],
    tool_event_queue: asyncio.Queue[ChatToolStartEvent | ChatToolEndEvent | ChatUsageEvent | None],
) -> tuple[str, list[str]]:
    """Run the agent and collect response content and SSE delta events.

    Args:
        message: The user message.
        history: The conversation history.
        tool_event_queue: Queue for tool/usage events from SSE callback.

    Returns:
        Tuple of (response_content, list of SSE event strings for deltas and tool events).
    """
    events: list[str] = []
    response_content = ""

    # Create SSE callback that adds tool/usage events to the queue
    def sse_callback(event: ChatToolStartEvent | ChatToolEndEvent | ChatUsageEvent) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            tool_event_queue.put_nowait(event)

    tool_registry = ToolRegistry(sse_callback=sse_callback)

    try:
        async for chunk in stream_response(
            message,
            conversation_history=history,
            tool_registry=tool_registry,
            sse_callback=sse_callback,
        ):
            # Drain pending tool events
            while not tool_event_queue.empty():
                try:
                    tool_event = tool_event_queue.get_nowait()
                    if tool_event is not None:
                        events.append(serialize_sse_event(tool_event))
                except asyncio.QueueEmpty:
                    break

            if chunk.content:
                response_content += chunk.content
                events.append(serialize_sse_event(ChatDeltaEvent(content=chunk.content)))

        # Drain remaining tool events
        while not tool_event_queue.empty():
            try:
                tool_event = tool_event_queue.get_nowait()
                if tool_event is not None:
                    events.append(serialize_sse_event(tool_event))
            except asyncio.QueueEmpty:
                break
    finally:
        await tool_registry.close()

    return response_content, events


async def _generate_sse_stream(
    message: str,
    conversation_id: str,
    history: list[dict[str, str]],
    event_collector: list[str] | None = None,
) -> AsyncIterator[str]:
    """Generate an SSE stream for a chat request.

    Retries automatically when the agent returns empty content (e.g. model
    returned no text after tool use). Tool events from failed attempts are
    still streamed to the client so they can see progress.

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

    try:
        # +2: range is exclusive and attempt 1 is the first try
        for attempt in range(1, MAX_EMPTY_RESPONSE_RETRIES + 2):
            response_content, events = await _stream_agent_response(message, history, tool_event_queue)

            # Emit all events from this attempt (tool events + deltas)
            for event_str in events:
                collect(event_str)
                yield event_str

            # If we got content, we're done
            if response_content.strip():
                break

            # Empty response — decide whether to retry
            if attempt <= MAX_EMPTY_RESPONSE_RETRIES:
                logger.warning(
                    "Agent returned empty response, retrying",
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
            else:
                logger.warning(
                    "Agent returned empty response after all retries",
                    extra={
                        "conversation_id": conversation_id,
                        "attempts": attempt,
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
