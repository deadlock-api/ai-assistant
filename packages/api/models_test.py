# Deadlock AI Assistant - SSE event model tests

import json

from packages.api.models import (
    ChatDeltaEvent,
    ChatEndEvent,
    ChatErrorEvent,
    ChatStartEvent,
    serialize_sse_event,
)


class TestChatStartEvent:
    """Tests for ChatStartEvent model."""

    def test_event_type_is_start(self) -> None:
        """Test that event type is 'start'."""
        event = ChatStartEvent(conversation_id="abc-123")
        assert event.event == "start"

    def test_includes_conversation_id(self) -> None:
        """Test that event includes conversation_id."""
        event = ChatStartEvent(conversation_id="abc-123")
        assert event.conversation_id == "abc-123"

    def test_serializes_to_json(self) -> None:
        """Test that event serializes correctly to JSON."""
        event = ChatStartEvent(conversation_id="abc-123")
        data = json.loads(event.model_dump_json())
        assert data == {"event": "start", "conversation_id": "abc-123"}


class TestChatDeltaEvent:
    """Tests for ChatDeltaEvent model."""

    def test_event_type_is_delta(self) -> None:
        """Test that event type is 'delta'."""
        event = ChatDeltaEvent(content="Hello")
        assert event.event == "delta"

    def test_includes_content(self) -> None:
        """Test that event includes content."""
        event = ChatDeltaEvent(content="Hello, world!")
        assert event.content == "Hello, world!"

    def test_serializes_to_json(self) -> None:
        """Test that event serializes correctly to JSON."""
        event = ChatDeltaEvent(content="test content")
        data = json.loads(event.model_dump_json())
        assert data == {"event": "delta", "content": "test content"}

    def test_empty_content_allowed(self) -> None:
        """Test that empty content is allowed."""
        event = ChatDeltaEvent(content="")
        assert event.content == ""


class TestChatEndEvent:
    """Tests for ChatEndEvent model."""

    def test_event_type_is_end(self) -> None:
        """Test that event type is 'end'."""
        event = ChatEndEvent(conversation_id="abc-123")
        assert event.event == "end"

    def test_includes_conversation_id(self) -> None:
        """Test that event includes conversation_id."""
        event = ChatEndEvent(conversation_id="xyz-789")
        assert event.conversation_id == "xyz-789"

    def test_serializes_to_json(self) -> None:
        """Test that event serializes correctly to JSON."""
        event = ChatEndEvent(conversation_id="abc-123")
        data = json.loads(event.model_dump_json())
        assert data == {"event": "end", "conversation_id": "abc-123"}


class TestChatErrorEvent:
    """Tests for ChatErrorEvent model."""

    def test_event_type_is_error(self) -> None:
        """Test that event type is 'error'."""
        event = ChatErrorEvent(error="Something went wrong", code="INTERNAL_ERROR")
        assert event.event == "error"

    def test_includes_error_message(self) -> None:
        """Test that event includes error message."""
        event = ChatErrorEvent(error="Connection failed", code="REDIS_ERROR")
        assert event.error == "Connection failed"

    def test_includes_error_code(self) -> None:
        """Test that event includes error code."""
        event = ChatErrorEvent(error="Invalid token", code="AUTH_FAILED")
        assert event.code == "AUTH_FAILED"

    def test_serializes_to_json(self) -> None:
        """Test that event serializes correctly to JSON."""
        event = ChatErrorEvent(error="Test error", code="TEST_CODE")
        data = json.loads(event.model_dump_json())
        assert data == {"event": "error", "error": "Test error", "code": "TEST_CODE"}


class TestSerializeSSEEvent:
    """Tests for serialize_sse_event helper function."""

    def test_start_event_format(self) -> None:
        """Test that start event is serialized in SSE format."""
        event = ChatStartEvent(conversation_id="abc-123")
        result = serialize_sse_event(event)
        assert result == 'data: {"event":"start","conversation_id":"abc-123"}\n\n'

    def test_delta_event_format(self) -> None:
        """Test that delta event is serialized in SSE format."""
        event = ChatDeltaEvent(content="Hello")
        result = serialize_sse_event(event)
        assert result == 'data: {"event":"delta","content":"Hello"}\n\n'

    def test_end_event_format(self) -> None:
        """Test that end event is serialized in SSE format."""
        event = ChatEndEvent(conversation_id="abc-123")
        result = serialize_sse_event(event)
        assert result == 'data: {"event":"end","conversation_id":"abc-123"}\n\n'

    def test_error_event_format(self) -> None:
        """Test that error event is serialized in SSE format."""
        event = ChatErrorEvent(error="Test error", code="TEST_CODE")
        result = serialize_sse_event(event)
        assert result == 'data: {"event":"error","error":"Test error","code":"TEST_CODE"}\n\n'

    def test_format_has_data_prefix(self) -> None:
        """Test that serialized event starts with 'data: '."""
        event = ChatDeltaEvent(content="test")
        result = serialize_sse_event(event)
        assert result.startswith("data: ")

    def test_format_ends_with_double_newline(self) -> None:
        """Test that serialized event ends with double newline."""
        event = ChatDeltaEvent(content="test")
        result = serialize_sse_event(event)
        assert result.endswith("\n\n")

    def test_content_is_valid_json(self) -> None:
        """Test that content between 'data: ' and newlines is valid JSON."""
        event = ChatStartEvent(conversation_id="test-id")
        result = serialize_sse_event(event)
        # Extract JSON part (after "data: " and before "\n\n")
        json_str = result[6:-2]
        data = json.loads(json_str)
        assert data["event"] == "start"
        assert data["conversation_id"] == "test-id"

    def test_special_characters_in_content(self) -> None:
        """Test that special characters in content are properly escaped."""
        event = ChatDeltaEvent(content='Hello "world" \n test')
        result = serialize_sse_event(event)
        # Should be valid JSON with escaped characters
        json_str = result[6:-2]
        data = json.loads(json_str)
        assert data["content"] == 'Hello "world" \n test'
