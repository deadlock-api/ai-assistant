# Deadlock AI Assistant - DateTime tools package
# Tools for working with dates, times, and timestamps

import re
import time
from typing import Any

from packages.tools.base import BaseTool, SSECallback


class DateTimeTool(BaseTool):
    """Tool to get current and relative Unix timestamps.

    Supports getting the current timestamp or calculating timestamps
    relative to now (e.g., "2 weeks ago", "14 days ago").
    """

    # Pattern to parse relative time expressions
    # Matches: "5 days ago", "2 weeks ago", "1 hour ago", etc.
    RELATIVE_PATTERN = re.compile(
        r"^\s*(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago\s*$",
        re.IGNORECASE,
    )

    # Time unit to seconds conversion
    TIME_UNITS: dict[str, int] = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
        "week": 604800,
        "month": 2592000,  # 30 days
        "year": 31536000,  # 365 days
    }

    def __init__(self, sse_callback: SSECallback, timeout: float = 30.0) -> None:
        super().__init__(sse_callback, timeout)

    @property
    def name(self) -> str:
        return "datetime_timestamp"

    async def _run(self, **kwargs: Any) -> dict[str, Any]:
        """Get current or relative Unix timestamp.

        Args:
            **kwargs: Tool arguments
                - relative: Optional relative time expression (e.g., "2 weeks ago", "14 days ago").
                            If not provided, returns the current timestamp.

        Returns:
            Dictionary with timestamp information:
            - timestamp: Unix timestamp (integer seconds since epoch)
            - relative: The relative expression used (if any)
            - description: Human-readable description of the timestamp

        Raises:
            ValueError: If relative expression is invalid
        """
        relative: str | None = kwargs.get("relative")
        current_time = int(time.time())

        if relative is None or relative.strip() == "":
            return {
                "timestamp": current_time,
                "relative": None,
                "description": "Current Unix timestamp",
            }

        # Parse relative expression
        match = self.RELATIVE_PATTERN.match(relative.strip())
        if not match:
            raise ValueError(
                f"Invalid relative time expression: '{relative}'. "
                "Expected format: '<number> <unit> ago' (e.g., '2 weeks ago', '14 days ago'). "
                "Supported units: second, minute, hour, day, week, month, year"
            )

        amount = int(match.group(1))
        unit = match.group(2).lower()

        if unit not in self.TIME_UNITS:
            raise ValueError(f"Unknown time unit: '{unit}'")

        seconds_offset = amount * self.TIME_UNITS[unit]
        relative_timestamp = current_time - seconds_offset

        # Build description
        unit_display = unit if amount == 1 else f"{unit}s"
        description = f"Unix timestamp from {amount} {unit_display} ago"

        return {
            "timestamp": relative_timestamp,
            "relative": relative.strip(),
            "description": description,
        }

    def _create_result_summary(self, result: dict[str, Any]) -> str:
        """Create a summary showing the timestamp."""
        return f"Timestamp: {result['timestamp']}"

    def get_definition(self) -> dict[str, Any]:
        """Get the tool definition for agent configuration.

        Returns:
            Tool definition dictionary
        """
        return {
            "name": self.name,
            "description": (
                "Get Unix timestamps (seconds since epoch). "
                "Can return the current timestamp or calculate a timestamp relative to now. "
                "Examples: no argument for current time, '2 weeks ago', '14 days ago', '1 hour ago'. "
                "Supported units: second, minute, hour, day, week, month, year."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "relative": {
                        "type": "string",
                        "description": (
                            "Optional relative time expression. "
                            "Format: '<number> <unit> ago' (e.g., '2 weeks ago', '14 days ago'). "
                            "If omitted, returns the current timestamp."
                        ),
                    }
                },
                "required": [],
            },
        }


__all__ = [
    "DateTimeTool",
]
