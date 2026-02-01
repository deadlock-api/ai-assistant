# Deadlock AI Assistant - Assets API tools
# Dynamically generated tools from the Deadlock Assets API OpenAPI specification

from typing import Any

import httpx

from packages.tools.base import BaseTool, SSECallback, retry
from packages.tools.openapi import OpenAPIConnectionError, OpenAPITool, OpenAPIToolGenerator

# Assets API configuration
ASSETS_API_SPEC_URL = "https://assets.deadlock-api.com/openapi.json"
ASSETS_API_BASE_URL = "https://assets.deadlock-api.com"
ASSETS_API_TOOL_PREFIX = "assets"


class AssetsAPIToolGenerator(OpenAPIToolGenerator):
    """Tool generator specifically for the Deadlock Assets API.

    Fetches the OpenAPI spec from https://assets.deadlock-api.com/openapi.json
    and generates tools for each endpoint. Tool names are prefixed with
    'assets_' (e.g., assets_get_heroes).

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 60.0)
    """

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            spec_url=ASSETS_API_SPEC_URL,
            sse_callback=sse_callback,
            tool_prefix=ASSETS_API_TOOL_PREFIX,
            timeout=timeout,
        )


class GetHeroNameTool(BaseTool):
    """Tool to get a hero's name from their ID.

    A convenient helper that fetches hero data from the Assets API
    and extracts just the name.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 60.0)
    """

    def __init__(self, sse_callback: SSECallback, timeout: float = 60.0) -> None:
        super().__init__(sse_callback, timeout)
        self._http_client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "get_hero_name"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self, hero_id: int) -> str:
        """Fetch hero data and return the name.

        Args:
            hero_id: The hero's numeric ID (must be a positive integer)

        Returns:
            The hero's name as a string

        Raises:
            ValueError: If hero_id is not a positive integer
            OpenAPIConnectionError: If the API call fails or hero not found
        """
        # Validate hero_id is a positive integer
        if not isinstance(hero_id, int) or hero_id <= 0:
            raise ValueError("hero_id must be a positive integer")

        url = f"{ASSETS_API_BASE_URL}/v2/heroes/{hero_id}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            # Extract the hero name
            hero_name = data.get("name")
            if not hero_name:
                raise OpenAPIConnectionError(f"Hero with ID {hero_id} has no name field")

            return str(hero_name)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise OpenAPIConnectionError(f"Hero with ID {hero_id} not found") from e
            raise OpenAPIConnectionError(f"API error: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise OpenAPIConnectionError(f"Network error: {e}") from e

    def _create_result_summary(self, result: str) -> str:
        """Return the hero name as the summary."""
        return f"Hero name: {result}"

    def get_definition(self) -> dict[str, Any]:
        """Get tool definition for agent configuration."""
        return {
            "name": self.name,
            "description": "Get a hero's name from their numeric ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "hero_id": {
                        "type": "integer",
                        "description": "The hero's numeric ID (must be a positive integer)",
                    },
                },
                "required": ["hero_id"],
            },
        }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


class GetItemNameTool(BaseTool):
    """Tool to get an item's name from its ID.

    A convenient helper that fetches item data from the Assets API
    and extracts just the name.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 60.0)
    """

    def __init__(self, sse_callback: SSECallback, timeout: float = 60.0) -> None:
        super().__init__(sse_callback, timeout)
        self._http_client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "get_item_name"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self, item_id: int) -> str:
        """Fetch item data and return the name.

        Args:
            item_id: The item's numeric ID (must be a positive integer)

        Returns:
            The item's name as a string

        Raises:
            ValueError: If item_id is not a positive integer
            OpenAPIConnectionError: If the API call fails or item not found
        """
        # Validate item_id is a positive integer
        if not isinstance(item_id, int) or item_id <= 0:
            raise ValueError("item_id must be a positive integer")

        url = f"{ASSETS_API_BASE_URL}/v2/items/{item_id}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            # Extract the item name
            item_name = data.get("name")
            if not item_name:
                raise OpenAPIConnectionError(f"Item with ID {item_id} has no name field")

            return str(item_name)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise OpenAPIConnectionError(f"Item with ID {item_id} not found") from e
            raise OpenAPIConnectionError(f"API error: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise OpenAPIConnectionError(f"Network error: {e}") from e

    def _create_result_summary(self, result: str) -> str:
        """Return the item name as the summary."""
        return f"Item name: {result}"

    def get_definition(self) -> dict[str, Any]:
        """Get tool definition for agent configuration."""
        return {
            "name": self.name,
            "description": "Get an item's name from its numeric ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "integer",
                        "description": "The item's numeric ID (must be a positive integer)",
                    },
                },
                "required": ["item_id"],
            },
        }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


async def create_assets_api_tools(
    sse_callback: SSECallback,
    timeout: float = 60.0,
) -> dict[str, OpenAPITool]:
    """Create all Assets API tools from the OpenAPI spec.

    This is a convenience function that creates an AssetsAPIToolGenerator,
    fetches the spec, and generates all tools.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 60.0)

    Returns:
        Dictionary mapping tool names to OpenAPITool instances
    """
    generator = AssetsAPIToolGenerator(sse_callback=sse_callback, timeout=timeout)
    return await generator.generate_tools()


__all__ = [
    "AssetsAPIToolGenerator",
    "GetHeroNameTool",
    "GetItemNameTool",
    "create_assets_api_tools",
    "ASSETS_API_SPEC_URL",
    "ASSETS_API_BASE_URL",
    "ASSETS_API_TOOL_PREFIX",
]
