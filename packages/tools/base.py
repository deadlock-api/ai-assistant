# Deadlock AI Assistant - Base tool infrastructure

import asyncio
import functools
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from packages.api.models import ChatToolEndEvent, ChatToolStartEvent


def retry(
    max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 60.0
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for retry with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay between retries in seconds (default: 1.0)
        max_delay: Maximum delay between retries in seconds (default: 60.0)
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2**attempt), max_delay)
                        await asyncio.sleep(delay)
            if last_exception:
                raise last_exception
            raise RuntimeError("Retry exhausted without exception")

        return wrapper

    return decorator


type SSECallback = Callable[[ChatToolStartEvent | ChatToolEndEvent], None]


class BaseTool(ABC):
    """Abstract base class for all tools.

    Tools extend this class to implement specific functionality while
    getting automatic SSE event emission and consistent error handling.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for tool execution (default: 60.0)
    """

    def __init__(self, sse_callback: SSECallback, timeout: float = 60.0) -> None:
        self._sse_callback = sse_callback
        self._timeout = timeout

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the tool name used in events and registration."""
        ...

    @abstractmethod
    async def _run(self, **kwargs: Any) -> Any:
        """Execute the tool logic. Subclasses must implement this.

        Args:
            **kwargs: Tool-specific arguments

        Returns:
            Tool-specific result
        """
        ...

    def _create_result_summary(self, result: Any) -> str:
        """Create a summary of the result for the end event.

        Override in subclasses for custom summaries.
        """
        if result is None:
            return "No result"
        if isinstance(result, str):
            return result[:100] + "..." if len(result) > 100 else result
        if isinstance(result, list):
            return f"List with {len(result)} items"
        if isinstance(result, dict):
            return f"Dict with {len(result)} keys"
        return str(result)[:100]

    async def execute(self, **kwargs: Any) -> Any:
        """Execute the tool with SSE event emission and timeout.

        Emits ChatToolStartEvent before execution and ChatToolEndEvent
        after, regardless of success or failure.

        Args:
            **kwargs: Tool-specific arguments

        Returns:
            Tool-specific result
        """
        # Emit start event
        start_event = ChatToolStartEvent(tool_name=self.name, arguments=kwargs)
        self._sse_callback(start_event)

        success = False
        result_summary = ""
        try:
            result = await asyncio.wait_for(self._run(**kwargs), timeout=self._timeout)
            success = True
            result_summary = self._create_result_summary(result)
            return result
        except TimeoutError:
            result_summary = f"Tool execution timed out after {self._timeout}s"
            raise
        except Exception as e:
            result_summary = f"Error: {e!s}"
            raise
        finally:
            end_event = ChatToolEndEvent(tool_name=self.name, success=success, result_summary=result_summary)
            self._sse_callback(end_event)
