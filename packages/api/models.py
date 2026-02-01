# Deadlock AI Assistant - API models

from typing import Literal

from pydantic import BaseModel


class ChatStartEvent(BaseModel):
    """SSE event sent when a chat response stream begins."""

    event: Literal["start"] = "start"
    conversation_id: str


class ChatDeltaEvent(BaseModel):
    """SSE event sent for each content chunk during streaming."""

    event: Literal["delta"] = "delta"
    content: str


class ChatEndEvent(BaseModel):
    """SSE event sent when a chat response stream completes."""

    event: Literal["end"] = "end"
    conversation_id: str


class ChatErrorEvent(BaseModel):
    """SSE event sent when an error occurs during streaming."""

    event: Literal["error"] = "error"
    error: str
    code: str


class ChatToolStartEvent(BaseModel):
    """SSE event sent when a tool invocation begins."""

    event: Literal["tool_start"] = "tool_start"
    tool_name: str
    arguments: dict[str, object]


class ChatToolEndEvent(BaseModel):
    """SSE event sent when a tool invocation completes."""

    event: Literal["tool_end"] = "tool_end"
    tool_name: str
    success: bool
    result_summary: str


type SSEEvent = ChatStartEvent | ChatDeltaEvent | ChatEndEvent | ChatErrorEvent | ChatToolStartEvent | ChatToolEndEvent


def serialize_sse_event(event: SSEEvent) -> str:
    """Serialize an SSE event to the standard SSE format.

    Returns a string in the format: data: {json}\n\n
    """
    return f"data: {event.model_dump_json()}\n\n"
