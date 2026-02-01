# Deadlock AI Assistant - DateTime Tools Unit Tests

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest

from packages.api.models import ChatToolEndEvent, ChatToolStartEvent
from packages.tools.datetime import DateTimeTool


class TestDateTimeTool:
    """Tests for DateTimeTool."""

    @pytest.fixture
    def sse_events(self) -> list[ChatToolStartEvent | ChatToolEndEvent]:
        """Capture SSE events."""
        return []

    @pytest.fixture
    def sse_callback(self, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]) -> Any:
        """Create SSE callback that captures events."""

        def callback(event: ChatToolStartEvent | ChatToolEndEvent) -> None:
            sse_events.append(event)

        return callback

    @pytest.fixture
    def datetime_tool(self, sse_callback: Any) -> DateTimeTool:
        """Create DateTimeTool instance."""
        return DateTimeTool(sse_callback=sse_callback, timeout=10.0)

    @pytest.mark.asyncio
    async def test_current_timestamp(self, datetime_tool: DateTimeTool) -> None:
        """Test getting current timestamp with no arguments."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run()

        assert result["timestamp"] == 1700000000
        assert result["relative"] is None
        assert "Current" in result["description"]

    @pytest.mark.asyncio
    async def test_current_timestamp_with_empty_string(self, datetime_tool: DateTimeTool) -> None:
        """Test getting current timestamp with empty string argument."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="")

        assert result["timestamp"] == 1700000000
        assert result["relative"] is None

    @pytest.mark.asyncio
    async def test_current_timestamp_with_whitespace(self, datetime_tool: DateTimeTool) -> None:
        """Test getting current timestamp with whitespace-only argument."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="   ")

        assert result["timestamp"] == 1700000000
        assert result["relative"] is None

    @pytest.mark.asyncio
    async def test_relative_seconds_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test relative timestamp with seconds."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="30 seconds ago")

        assert result["timestamp"] == 1700000000 - 30
        assert result["relative"] == "30 seconds ago"
        assert "30 seconds ago" in result["description"]

    @pytest.mark.asyncio
    async def test_relative_minute_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test relative timestamp with singular minute."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="1 minute ago")

        assert result["timestamp"] == 1700000000 - 60
        assert result["relative"] == "1 minute ago"
        assert "1 minute ago" in result["description"]

    @pytest.mark.asyncio
    async def test_relative_minutes_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test relative timestamp with plural minutes."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="5 minutes ago")

        assert result["timestamp"] == 1700000000 - (5 * 60)

    @pytest.mark.asyncio
    async def test_relative_hours_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test relative timestamp with hours."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="2 hours ago")

        assert result["timestamp"] == 1700000000 - (2 * 3600)

    @pytest.mark.asyncio
    async def test_relative_days_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test relative timestamp with days."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="14 days ago")

        assert result["timestamp"] == 1700000000 - (14 * 86400)

    @pytest.mark.asyncio
    async def test_relative_weeks_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test relative timestamp with weeks."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="2 weeks ago")

        assert result["timestamp"] == 1700000000 - (2 * 604800)

    @pytest.mark.asyncio
    async def test_relative_months_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test relative timestamp with months (30 days)."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="3 months ago")

        assert result["timestamp"] == 1700000000 - (3 * 2592000)

    @pytest.mark.asyncio
    async def test_relative_years_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test relative timestamp with years (365 days)."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="1 year ago")

        assert result["timestamp"] == 1700000000 - 31536000

    @pytest.mark.asyncio
    async def test_relative_case_insensitive(self, datetime_tool: DateTimeTool) -> None:
        """Test relative expression is case insensitive."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result1 = await datetime_tool._run(relative="2 WEEKS AGO")
            result2 = await datetime_tool._run(relative="2 Weeks Ago")
            result3 = await datetime_tool._run(relative="2 weeks ago")

        expected = 1700000000 - (2 * 604800)
        assert result1["timestamp"] == expected
        assert result2["timestamp"] == expected
        assert result3["timestamp"] == expected

    @pytest.mark.asyncio
    async def test_relative_with_extra_whitespace(self, datetime_tool: DateTimeTool) -> None:
        """Test relative expression with extra whitespace."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="  2   weeks   ago  ")

        assert result["timestamp"] == 1700000000 - (2 * 604800)

    @pytest.mark.asyncio
    async def test_invalid_relative_format(self, datetime_tool: DateTimeTool) -> None:
        """Test error for invalid relative format."""
        with pytest.raises(ValueError) as exc_info:
            await datetime_tool._run(relative="yesterday")
        assert "Invalid relative time expression" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_relative_no_number(self, datetime_tool: DateTimeTool) -> None:
        """Test error for relative expression without number."""
        with pytest.raises(ValueError) as exc_info:
            await datetime_tool._run(relative="weeks ago")
        assert "Invalid relative time expression" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_relative_no_unit(self, datetime_tool: DateTimeTool) -> None:
        """Test error for relative expression without unit."""
        with pytest.raises(ValueError) as exc_info:
            await datetime_tool._run(relative="5 ago")
        assert "Invalid relative time expression" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_relative_no_ago(self, datetime_tool: DateTimeTool) -> None:
        """Test error for relative expression without 'ago'."""
        with pytest.raises(ValueError) as exc_info:
            await datetime_tool._run(relative="5 days")
        assert "Invalid relative time expression" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_unit(self, datetime_tool: DateTimeTool) -> None:
        """Test error for unknown time unit."""
        with pytest.raises(ValueError) as exc_info:
            await datetime_tool._run(relative="5 centuries ago")
        assert "Invalid relative time expression" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self,
        datetime_tool: DateTimeTool,
        sse_events: list[ChatToolStartEvent | ChatToolEndEvent],
    ) -> None:
        """Test execute emits start and end SSE events."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            await datetime_tool.execute()

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "datetime_timestamp"
        assert start_event.arguments == {}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.tool_name == "datetime_timestamp"
        assert end_event.success is True
        assert "1700000000" in end_event.result_summary

    @pytest.mark.asyncio
    async def test_execute_with_relative_emits_sse_events(
        self,
        datetime_tool: DateTimeTool,
        sse_events: list[ChatToolStartEvent | ChatToolEndEvent],
    ) -> None:
        """Test execute with relative arg emits SSE events."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            await datetime_tool.execute(relative="2 weeks ago")

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.arguments == {"relative": "2 weeks ago"}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is True

    @pytest.mark.asyncio
    async def test_execute_emits_error_event_on_failure(
        self,
        datetime_tool: DateTimeTool,
        sse_events: list[ChatToolStartEvent | ChatToolEndEvent],
    ) -> None:
        """Test execute emits error end event on failure."""
        with pytest.raises(ValueError):
            await datetime_tool.execute(relative="invalid")

        assert len(sse_events) == 2
        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is False
        assert "Error:" in end_event.result_summary

    def test_name_property(self, datetime_tool: DateTimeTool) -> None:
        """Test name property returns correct name."""
        assert datetime_tool.name == "datetime_timestamp"

    def test_result_summary(self, datetime_tool: DateTimeTool) -> None:
        """Test result summary formatting."""
        result = {"timestamp": 1700000000, "relative": None, "description": "Current"}
        summary = datetime_tool._create_result_summary(result)
        assert "1700000000" in summary

    def test_get_definition(self, datetime_tool: DateTimeTool) -> None:
        """Test get_definition returns correct tool definition."""
        definition = datetime_tool.get_definition()
        assert definition["name"] == "datetime_timestamp"
        assert "description" in definition
        assert "Unix timestamp" in definition["description"]
        assert "relative" in definition["parameters"]["properties"]
        assert definition["parameters"]["required"] == []

    def test_get_definition_includes_examples(self, datetime_tool: DateTimeTool) -> None:
        """Test that definition includes usage examples."""
        definition = datetime_tool.get_definition()
        description = definition["description"]
        assert "weeks ago" in description or "days ago" in description

    @pytest.mark.asyncio
    async def test_timestamp_is_integer(self, datetime_tool: DateTimeTool) -> None:
        """Test that timestamp is always an integer."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.123):
            result = await datetime_tool._run()

        assert isinstance(result["timestamp"], int)
        assert result["timestamp"] == 1700000000

    @pytest.mark.asyncio
    async def test_description_singular_unit(self, datetime_tool: DateTimeTool) -> None:
        """Test description uses singular unit for 1."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="1 day ago")

        assert "1 day ago" in result["description"]
        # Should not say "1 days"

    @pytest.mark.asyncio
    async def test_description_plural_unit(self, datetime_tool: DateTimeTool) -> None:
        """Test description uses plural unit for > 1."""
        with patch("packages.tools.datetime.time.time", return_value=1700000000.0):
            result = await datetime_tool._run(relative="2 days ago")

        assert "2 days ago" in result["description"]


class TestDateTimeToolIntegration:
    """Integration tests for DateTimeTool."""

    @pytest.fixture
    def sse_events(self) -> list[ChatToolStartEvent | ChatToolEndEvent]:
        """Capture SSE events."""
        return []

    @pytest.fixture
    def sse_callback(self, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]) -> Any:
        """Create SSE callback that captures events."""

        def callback(event: ChatToolStartEvent | ChatToolEndEvent) -> None:
            sse_events.append(event)

        return callback

    @pytest.mark.asyncio
    async def test_real_current_timestamp(self, sse_callback: Any) -> None:
        """Test getting actual current timestamp (no mocking)."""
        tool = DateTimeTool(sse_callback=sse_callback)

        before = int(time.time())
        result = await tool._run()
        after = int(time.time())

        # Timestamp should be between before and after
        assert before <= result["timestamp"] <= after

    @pytest.mark.asyncio
    async def test_real_relative_timestamp(self, sse_callback: Any) -> None:
        """Test real relative timestamp calculation."""
        tool = DateTimeTool(sse_callback=sse_callback)

        current_result = await tool._run()
        relative_result = await tool._run(relative="1 hour ago")

        # Relative should be approximately 1 hour (3600 seconds) before current
        diff = current_result["timestamp"] - relative_result["timestamp"]
        assert 3599 <= diff <= 3601  # Allow for 1 second timing variance
