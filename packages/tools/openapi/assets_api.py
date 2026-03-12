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


class GetHeroMappingTool(BaseTool):
    """Tool to get the complete hero name to ID mapping.

    Returns a dictionary mapping hero names to their IDs for all active
    heroes in the game.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 60.0)
    """

    def __init__(self, sse_callback: SSECallback, timeout: float = 60.0) -> None:
        super().__init__(sse_callback, timeout)
        self._http_client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "get_hero_mapping"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self) -> dict[str, int]:
        """Fetch all heroes and return the name to ID mapping.

        Returns:
            dict mapping hero names to their IDs

        Raises:
            OpenAPIConnectionError: If the API call fails
        """
        url = f"{ASSETS_API_BASE_URL}/v2/heroes"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            heroes: list[dict[str, Any]] = response.json()

            name_to_id: dict[str, int] = {}

            for hero in heroes:
                hero_id = hero.get("id")
                hero_name = hero.get("name")
                if hero_id is not None and hero_name:
                    name_to_id[hero_name] = hero_id

            return name_to_id

        except httpx.HTTPStatusError as e:
            raise OpenAPIConnectionError(f"API error: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise OpenAPIConnectionError(f"Network error: {e}") from e

    def _create_result_summary(self, result: dict[str, int]) -> str:
        """Return a summary of the mapping."""
        return f"Hero mapping: {len(result)} heroes"

    def get_definition(self) -> dict[str, Any]:
        """Get tool definition for agent configuration."""
        return {
            "name": self.name,
            "description": "Get the complete hero name to ID mapping for all heroes",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


class GetItemMappingTool(BaseTool):
    """Tool to get the complete item name to ID mapping.

    Returns a dictionary mapping item names to their IDs for all items
    in the game.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 60.0)
    """

    def __init__(self, sse_callback: SSECallback, timeout: float = 60.0) -> None:
        super().__init__(sse_callback, timeout)
        self._http_client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "get_item_mapping"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self) -> dict[str, int]:
        """Fetch all items and return the name to ID mapping.

        Returns:
            dict mapping item names to their IDs

        Raises:
            OpenAPIConnectionError: If the API call fails
        """
        url = f"{ASSETS_API_BASE_URL}/v2/items"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            items: list[dict[str, Any]] = response.json()

            name_to_id: dict[str, int] = {}

            for item in items:
                item_id = item.get("id")
                item_name = item.get("name")
                if item_id is not None and item_name:
                    name_to_id[item_name] = item_id

            return name_to_id

        except httpx.HTTPStatusError as e:
            raise OpenAPIConnectionError(f"API error: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise OpenAPIConnectionError(f"Network error: {e}") from e

    def _create_result_summary(self, result: dict[str, int]) -> str:
        """Return a summary of the mapping."""
        return f"Item mapping: {len(result)} items"

    def get_definition(self) -> dict[str, Any]:
        """Get tool definition for agent configuration."""
        return {
            "name": self.name,
            "description": "Get the complete item name to ID mapping for all items",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
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


# Valid HTTP methods for the generic API caller
VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})


class AssetsAPICallTool(BaseTool):
    """Generic tool for calling any Deadlock Assets API endpoint.

    Replaces individual auto-generated endpoint tools with a single generic caller
    to reduce token usage from tool definitions.
    """

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(sse_callback, timeout)
        self._http_client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "assets_api_call"

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
                "Call any Deadlock Assets API endpoint (assets.deadlock-api.com). "
                "This API has hero details, item details, ability info, and game metadata. "
                "Use this (not deadlock_api_call) for /v2/heroes/ and /v2/items/ endpoints. "
                "Use deadlock_api_list_endpoints with api='assets' to discover available endpoints."
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
                        "description": "API path starting with / (e.g., /v2/heroes/17)",
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
        method_upper = method.upper()
        if method_upper not in VALID_HTTP_METHODS:
            raise ValueError(f"Invalid HTTP method: {method}. Must be one of: {', '.join(sorted(VALID_HTTP_METHODS))}")

        if not path.startswith("/"):
            raise ValueError(f"Path must start with /: {path}")

        url = ASSETS_API_BASE_URL + path

        try:
            client = await self._get_client()
            response = await client.request(
                method=method_upper,
                url=url,
                params=query_params,
                json=body,
            )
            response.raise_for_status()

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
    "AssetsAPICallTool",
    "AssetsAPIToolGenerator",
    "GetHeroMappingTool",
    "GetHeroNameTool",
    "GetItemMappingTool",
    "GetItemNameTool",
    "create_assets_api_tools",
    "ASSETS_API_SPEC_URL",
    "ASSETS_API_BASE_URL",
    "ASSETS_API_TOOL_PREFIX",
]
