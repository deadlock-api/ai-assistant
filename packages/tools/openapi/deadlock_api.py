# Deadlock AI Assistant - Deadlock API tools
# Dynamically generated tools from the Deadlock API OpenAPI specification


from typing import Any

import httpx

from packages.tools.base import BaseTool, SSECallback, retry
from packages.tools.openapi import OpenAPIConnectionError, OpenAPITool, OpenAPIToolGenerator

# Deadlock API configuration
DEADLOCK_API_SPEC_URL = "https://api.deadlock-api.com/openapi.json"
DEADLOCK_API_TOOL_PREFIX = "dl"

# Operations to exclude from Deadlock API tool generation
# We exclude SQL endpoint because we have a direct ClickHouse tool (clickhouse_query)
# that provides the same functionality without rate limits
DEADLOCK_API_EXCLUDED_OPERATIONS = {"sql"}


class DeadlockAPIToolGenerator(OpenAPIToolGenerator):
    """Tool generator specifically for the Deadlock API.

    Fetches the OpenAPI spec from https://api.deadlock-api.com/openapi.json
    and generates tools for each endpoint. Tool names are prefixed with
    'deadlock_api_' (e.g., deadlock_api_get_match).

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 30.0)
    """

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            spec_url=DEADLOCK_API_SPEC_URL,
            sse_callback=sse_callback,
            tool_prefix=DEADLOCK_API_TOOL_PREFIX,
            timeout=timeout,
            excluded_operations=DEADLOCK_API_EXCLUDED_OPERATIONS,
        )


async def create_deadlock_api_tools(
    sse_callback: SSECallback,
    timeout: float = 30.0,
) -> dict[str, OpenAPITool]:
    """Create all Deadlock API tools from the OpenAPI spec.

    This is a convenience function that creates a DeadlockAPIToolGenerator,
    fetches the spec, and generates all tools.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 30.0)

    Returns:
        Dictionary mapping tool names to OpenAPITool instances
    """
    generator = DeadlockAPIToolGenerator(sse_callback=sse_callback, timeout=timeout)
    return await generator.generate_tools()


# Valid HTTP methods for the generic API caller
VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})

# Base URL for the Deadlock API
DEADLOCK_API_BASE_URL = "https://api.deadlock-api.com"


class DeadlockAPICallTool(BaseTool):
    """Generic tool for calling any Deadlock API endpoint.

    Use this as a fallback when dynamic tools don't cover specific needs.
    Allows calling arbitrary endpoints with custom parameters.
    """

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(sse_callback, timeout)
        self._http_client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "deadlock_api_call"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    def get_definition(self) -> dict[str, Any]:
        """Get tool definition for agent configuration."""
        return {
            "name": self.name,
            "description": (
                "Generic tool to call any Deadlock API endpoint. "
                "Use this when specific API tools don't cover your needs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "enum": list(VALID_HTTP_METHODS),
                        "description": "HTTP method (GET, POST, PUT, DELETE, PATCH)",
                    },
                    "path": {
                        "type": "string",
                        "description": "API path starting with / (e.g., /v1/matches/123)",
                    },
                    "query_params": {
                        "type": "object",
                        "description": "Query parameters as key-value pairs",
                    },
                    "body": {
                        "type": "object",
                        "description": "Request body for POST/PUT/PATCH requests",
                    },
                },
                "required": ["method", "path"],
            },
        }

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(
        self,
        method: str,
        path: str,
        query_params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the API call.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            path: API path starting with /
            query_params: Optional query parameters
            body: Optional request body

        Returns:
            API response as dictionary

        Raises:
            ValueError: If method or path is invalid
            OpenAPIConnectionError: If API call fails
        """
        # Validate method
        method_upper = method.upper()
        if method_upper not in VALID_HTTP_METHODS:
            raise ValueError(f"Invalid HTTP method: {method}. Must be one of: {', '.join(sorted(VALID_HTTP_METHODS))}")

        # Validate path
        if not path.startswith("/"):
            raise ValueError(f"Path must start with /: {path}")

        # Build full URL
        url = DEADLOCK_API_BASE_URL + path

        try:
            client = await self._get_client()
            response = await client.request(
                method=method_upper,
                url=url,
                params=query_params,
                json=body,
            )
            response.raise_for_status()

            # Try to parse as JSON
            try:
                return response.json()
            except Exception:
                return {"content": response.text}

        except httpx.HTTPStatusError as e:
            raise OpenAPIConnectionError(f"API error: HTTP {e.response.status_code} - {e.response.text[:200]}") from e
        except httpx.RequestError as e:
            raise OpenAPIConnectionError(f"Network error: {e}") from e

    def _create_result_summary(self, result: dict[str, Any]) -> str:
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            return f"Response with {len(result)} fields"
        return str(result)[:100]

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


__all__ = [
    "DeadlockAPIToolGenerator",
    "DeadlockAPICallTool",
    "create_deadlock_api_tools",
    "DEADLOCK_API_SPEC_URL",
    "DEADLOCK_API_TOOL_PREFIX",
    "DEADLOCK_API_BASE_URL",
    "DEADLOCK_API_EXCLUDED_OPERATIONS",
    "VALID_HTTP_METHODS",
]
