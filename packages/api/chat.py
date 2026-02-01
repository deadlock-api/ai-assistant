# Deadlock AI Assistant - Chat Endpoint

from __future__ import annotations

import asyncio
import contextlib
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

router = APIRouter()


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    message: str
    conversation_id: str | None = None


async def _generate_sse_stream(
    message: str,
    conversation_id: str,
    history: list[dict[str, str]],
    event_collector: list[str] | None = None,
) -> AsyncIterator[str]:
    """Generate an SSE stream for a chat request.

    Tool events are interleaved with content delta events, allowing clients
    to see when tools are invoked and their results.

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

    # Create an async queue to collect tool events
    tool_event_queue: asyncio.Queue[ChatToolStartEvent | ChatToolEndEvent | None] = asyncio.Queue()

    # Create SSE callback that adds tool events to the queue
    def sse_callback(event: ChatToolStartEvent | ChatToolEndEvent) -> None:
        """Callback that queues tool events for SSE streaming."""
        with contextlib.suppress(asyncio.QueueFull):
            tool_event_queue.put_nowait(event)

    # Create tool registry with SSE callback
    tool_registry = ToolRegistry(sse_callback=sse_callback)

    # Collect response content for saving
    response_content = ""

    try:
        # Stream response from agent with tool registry
        async for chunk in stream_response(
            message,
            conversation_history=history,
            tool_registry=tool_registry,
            sse_callback=sse_callback,
        ):
            # First, emit any pending tool events
            while not tool_event_queue.empty():
                try:
                    tool_event = tool_event_queue.get_nowait()
                    if tool_event is not None:
                        event_str = serialize_sse_event(tool_event)
                        collect(event_str)
                        yield event_str
                except asyncio.QueueEmpty:
                    break

            # Then emit the content delta
            if chunk.content:
                response_content += chunk.content
                delta_event = serialize_sse_event(ChatDeltaEvent(content=chunk.content))
                collect(delta_event)
                yield delta_event

        # Emit any remaining tool events after streaming completes
        while not tool_event_queue.empty():
            try:
                tool_event = tool_event_queue.get_nowait()
                if tool_event is not None:
                    event_str = serialize_sse_event(tool_event)
                    collect(event_str)
                    yield event_str
            except asyncio.QueueEmpty:
                break

        # Save user message and assistant response to history
        await add_message(conversation_id, "user", message)
        await add_message(conversation_id, "assistant", response_content)

        # Send end event
        end_event = serialize_sse_event(ChatEndEvent(conversation_id=conversation_id))
        collect(end_event)
        yield end_event

    except AgentConfigurationError as e:
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_CONFIGURATION_ERROR"))
    except AgentTimeoutError as e:
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_TIMEOUT"))
    except AgentAuthError as e:
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_AUTH_ERROR"))
    except AgentRateLimitError as e:
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_RATE_LIMIT"))
    except AgentRetryExhaustedError as e:
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_RETRY_EXHAUSTED"))
    except AgentError as e:
        yield serialize_sse_event(ChatErrorEvent(error=str(e), code="AGENT_ERROR"))
    except RedisUnavailableError:
        yield serialize_sse_event(ChatErrorEvent(error="Failed to save conversation history", code="REDIS_ERROR"))
    finally:
        # Clean up tool registry connections
        await tool_registry.close()


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
            return StreamingResponse(
                replay_cached_stream(cached_events),
                media_type="text/event-stream",
            )
    except RedisUnavailableError:
        # Cache lookup failed, proceed without cache
        pass

    return StreamingResponse(
        _cached_sse_stream(request.message, conversation_id, history, cache_key),
        media_type="text/event-stream",
    )
