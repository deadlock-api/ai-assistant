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
        timeout: Timeout in seconds for API calls (default: 60.0)
    """

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float = 60.0,
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
    timeout: float = 60.0,
) -> dict[str, OpenAPITool]:
    """Create all Deadlock API tools from the OpenAPI spec.

    This is a convenience function that creates a DeadlockAPIToolGenerator,
    fetches the spec, and generates all tools.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 60.0)

    Returns:
        Dictionary mapping tool names to OpenAPITool instances
    """
    generator = DeadlockAPIToolGenerator(sse_callback=sse_callback, timeout=timeout)
    return await generator.generate_tools()


# Valid HTTP methods for the generic API caller
VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})

# Base URL for the Deadlock API
DEADLOCK_API_BASE_URL = "https://api.deadlock-api.com"

# Deadlock API resource URLs
DEADLOCK_API_INFO = {
    "overview": {
        "description": (
            "The Deadlock API is a community-driven, open-source project providing comprehensive data access "
            "for Valve's game Deadlock. It offers two main APIs: a Game Data API for match data, player stats, "
            "leaderboards, and analytics; and an Assets API for hero portraits, item icons, ability images, "
            "and other game media. Both APIs are free to use, well-documented via OpenAPI/Swagger specs, and "
            "do not require authentication for most endpoints."
        ),
    },
    "main_website": {
        "url": "https://deadlock-api.com/",
        "description": (
            "Main website for the Deadlock API project with overview, documentation links, and getting started guides"
        ),
    },
    "data_api": {
        "url": "https://api.deadlock-api.com/",
        "description": (
            "Game Data API — provides match data, player statistics, hero win rates, item pick rates, "
            "leaderboards, match histories, MMR tracking, and more. Endpoints include match details, "
            "player profiles, hero/item stats, and ranked leaderboards."
        ),
        "openapi_spec": "https://api.deadlock-api.com/openapi.json",
        "docs": "https://api.deadlock-api.com/docs",
    },
    "assets_api": {
        "url": "https://assets.deadlock-api.com/",
        "description": (
            "Assets API — provides game assets including hero portraits, ability icons, item icons, "
            "hero stats, item stats, ability details, and raw game data. Use this to get hero/item metadata, "
            "images, and detailed game balance information."
        ),
        "openapi_spec": "https://assets.deadlock-api.com/openapi.json",
        "docs": "https://assets.deadlock-api.com/docs",
    },
    "github": {
        "url": "https://github.com/deadlock-api/",
        "description": (
            "GitHub organization with source code, documentation, and community contributions "
            "for all Deadlock API projects"
        ),
    },
    "discord": {
        "url": "https://discord.gg/XMF9Xrgfqu",
        "description": "Discord community server for API support, bug reports, feature requests, and announcements",
    },
    "patreon": {
        "url": "https://www.patreon.com/user?u=68961896",
        "description": "Patreon page to support ongoing Deadlock API development and server costs",
    },
}

# OpenAPI spec URLs for schema fetching
OPENAPI_SPEC_URLS: dict[str, str] = {
    "data": "https://api.deadlock-api.com/openapi.json",
    "assets": "https://assets.deadlock-api.com/openapi.json",
}


class DeadlockAPIInfoTool(BaseTool):
    """Tool that returns information about the Deadlock API resources.

    Provides URLs and descriptions for the main website, assets API,
    data API, and GitHub repository.
    """

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(sse_callback, timeout)

    @property
    def name(self) -> str:
        return "deadlock_api_info"

    def get_definition(self) -> dict[str, Any]:
        """Get tool definition for agent configuration."""
        return {
            "name": self.name,
            "description": (
                "Use this tool when a user asks about the Deadlock API. "
                "Returns URLs and descriptions for all Deadlock API resources."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def _run(self, **kwargs: Any) -> dict[str, Any]:
        """Return information about the Deadlock API resources.

        Args:
            **kwargs: Unused, accepted for base class compatibility

        Returns:
            Dictionary containing resource names mapped to their URLs and descriptions
        """
        del kwargs  # Unused, no parameters for this tool
        return DEADLOCK_API_INFO

    def _create_result_summary(self, result: dict[str, Any]) -> str:
        return f"Returned info for {len(result)} Deadlock API resources"


class DeadlockAPIListEndpointsTool(BaseTool):
    """Tool that lists all available endpoints for a Deadlock API.

    Fetches the OpenAPI spec and returns a compact list of endpoints
    with method, path, and summary. Much cheaper than fetching the full schema.
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
        return "deadlock_api_list_endpoints"

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
                "List all available endpoints for a Deadlock API. Returns method, path, and summary "
                "for each endpoint. Use this to discover what endpoints are available before calling them. "
                "Pass api='data' for the Game Data API (api.deadlock-api.com) or "
                "api='assets' for the Assets API (assets.deadlock-api.com)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "api": {
                        "type": "string",
                        "enum": ["data", "assets"],
                        "description": (
                            "Which API to list endpoints for: "
                            "'data' for the Game Data API or "
                            "'assets' for the Assets API"
                        ),
                    },
                },
                "required": ["api"],
            },
        }

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self, api: str) -> list[dict[str, str]]:
        """List all endpoints for the specified API.

        Args:
            api: Which API to list endpoints for ('data' or 'assets')

        Returns:
            List of dicts with 'method', 'path', and 'summary' keys

        Raises:
            ValueError: If api is not 'data' or 'assets'
            OpenAPIConnectionError: If the spec cannot be fetched
        """
        if api not in OPENAPI_SPEC_URLS:
            raise ValueError(f"Invalid API name: {api}. Must be one of: {', '.join(sorted(OPENAPI_SPEC_URLS))}")

        spec_url = OPENAPI_SPEC_URLS[api]

        try:
            client = await self._get_client()
            response = await client.get(spec_url)
            response.raise_for_status()
            spec: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as e:
            raise OpenAPIConnectionError(f"Failed to fetch {api} API spec: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise OpenAPIConnectionError(f"Network error fetching {api} API spec: {e}") from e

        endpoints: list[dict[str, str]] = []
        paths: dict[str, Any] = spec.get("paths", {})
        for path, methods in sorted(paths.items()):
            for method, operation in methods.items():
                if method in ("get", "post", "put", "delete", "patch"):
                    endpoints.append(
                        {
                            "method": method.upper(),
                            "path": path,
                            "summary": operation.get("summary", ""),
                        }
                    )

        return endpoints

    def _create_result_summary(self, result: list[dict[str, str]]) -> str:
        return f"Listed {len(result)} endpoints"

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


class DeadlockAPIEndpointDetailsTool(BaseTool):
    """Tool that fetches details for a specific Deadlock API endpoint.

    Returns parameters, request body schema, and response schema for a
    single endpoint. Use after listing endpoints to get usage details.
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
        return "deadlock_api_endpoint_details"

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
                "Get detailed information about a specific Deadlock API endpoint, including "
                "parameters, request body, and response schema. Use deadlock_api_list_endpoints "
                "first to discover available endpoints, then use this to get details for a specific one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "api": {
                        "type": "string",
                        "enum": ["data", "assets"],
                        "description": (
                            "Which API the endpoint belongs to: "
                            "'data' for the Game Data API or "
                            "'assets' for the Assets API"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": "The endpoint path (e.g., '/v1/players/{account_id}/match-history')",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                        "description": "HTTP method (default: GET)",
                    },
                },
                "required": ["api", "path"],
            },
        }

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self, api: str, path: str, method: str = "GET") -> dict[str, Any]:
        """Get details for a specific endpoint.

        Args:
            api: Which API ('data' or 'assets')
            path: The endpoint path
            method: HTTP method (default: GET)

        Returns:
            Dict with endpoint details (parameters, requestBody, responses)

        Raises:
            ValueError: If api is invalid or endpoint not found
            OpenAPIConnectionError: If the spec cannot be fetched
        """
        if api not in OPENAPI_SPEC_URLS:
            raise ValueError(f"Invalid API name: {api}. Must be one of: {', '.join(sorted(OPENAPI_SPEC_URLS))}")

        spec_url = OPENAPI_SPEC_URLS[api]

        try:
            client = await self._get_client()
            response = await client.get(spec_url)
            response.raise_for_status()
            spec: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as e:
            raise OpenAPIConnectionError(f"Failed to fetch {api} API spec: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise OpenAPIConnectionError(f"Network error fetching {api} API spec: {e}") from e

        paths: dict[str, Any] = spec.get("paths", {})
        if path not in paths:
            raise ValueError(f"Endpoint '{path}' not found in {api} API. Use deadlock_api_list_endpoints to see all.")

        method_lower = method.lower()
        path_data: dict[str, Any] = paths[path]
        if method_lower not in path_data:
            available = [m.upper() for m in path_data if m in ("get", "post", "put", "delete", "patch")]
            raise ValueError(f"Method {method} not found for {path}. Available methods: {', '.join(available)}")

        operation: dict[str, Any] = path_data[method_lower]

        # Resolve $ref references in schemas for clarity
        components = spec.get("components", {})
        schemas = components.get("schemas", {})

        def resolve_ref(obj: Any) -> Any:
            """Resolve a single level of $ref references."""
            if isinstance(obj, dict):
                if "$ref" in obj:
                    ref_path = obj["$ref"]
                    # Handle #/components/schemas/SchemaName
                    if ref_path.startswith("#/components/schemas/"):
                        schema_name = ref_path.split("/")[-1]
                        if schema_name in schemas:
                            return schemas[schema_name]
                    return obj
                return {k: resolve_ref(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [resolve_ref(item) for item in obj]
            return obj

        result: dict[str, Any] = {
            "method": method.upper(),
            "path": path,
            "summary": operation.get("summary", ""),
            "description": operation.get("description", ""),
        }

        if "parameters" in operation:
            result["parameters"] = resolve_ref(operation["parameters"])

        if "requestBody" in operation:
            result["request_body"] = resolve_ref(operation["requestBody"])

        if "responses" in operation:
            result["responses"] = resolve_ref(operation["responses"])

        return result

    def _create_result_summary(self, result: dict[str, Any]) -> str:
        return f"Details for {result.get('method', '?')} {result.get('path', '?')}"

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


class DeadlockAPICallTool(BaseTool):
    """Generic tool for calling any Deadlock API endpoint.

    Use this as a fallback when dynamic tools don't cover specific needs.
    Allows calling arbitrary endpoints with custom parameters.
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
                "Call any Deadlock Game Data API endpoint (api.deadlock-api.com). "
                "Use deadlock_api_list_endpoints with api='data' to discover available endpoints, "
                "and deadlock_api_endpoint_details to get parameter details for a specific endpoint."
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
    "DeadlockAPIEndpointDetailsTool",
    "DeadlockAPIInfoTool",
    "DeadlockAPIListEndpointsTool",
    "create_deadlock_api_tools",
    "DEADLOCK_API_SPEC_URL",
    "DEADLOCK_API_TOOL_PREFIX",
    "DEADLOCK_API_BASE_URL",
    "DEADLOCK_API_EXCLUDED_OPERATIONS",
    "DEADLOCK_API_INFO",
    "OPENAPI_SPEC_URLS",
    "VALID_HTTP_METHODS",
]
