# Deadlock AI Assistant - OpenAPI tools package
# Tools for dynamically generating API tools from OpenAPI specifications

from typing import Any

import httpx

from packages.tools.base import BaseTool, SSECallback, retry


class OpenAPIConnectionError(Exception):
    """Raised when unable to fetch or parse an OpenAPI spec."""


class OpenAPITool(BaseTool):
    """A tool generated from an OpenAPI operation.

    Handles parameter substitution, validation, and API calls.
    """

    def __init__(
        self,
        name: str,
        path: str,
        method: str,
        base_url: str,
        parameters: dict[str, Any],
        description: str,
        sse_callback: SSECallback,
        timeout: float,
        http_client_getter: Any,
    ) -> None:
        super().__init__(sse_callback, timeout)
        self._name = name
        self._path = path
        self._method = method
        self._base_url = base_url.rstrip("/")
        self._parameters = parameters
        self._description = description
        self._http_client_getter = http_client_getter

    @property
    def name(self) -> str:
        return self._name

    def get_definition(self) -> dict[str, Any]:
        """Get tool definition for agent configuration."""
        # Build parameter schema from OpenAPI parameters
        properties: dict[str, Any] = {}
        required: list[str] = []

        # Add path parameters
        for param in self._parameters.get("path", []):
            param_name = param["name"]
            properties[param_name] = {
                "type": param["schema"].get("type", "string"),
                "description": param.get("description", ""),
            }
            if param.get("required", True):  # Path params are always required
                required.append(param_name)

        # Add query parameters
        for param in self._parameters.get("query", []):
            param_name = param["name"]
            properties[param_name] = {
                "type": param["schema"].get("type", "string"),
                "description": param.get("description", ""),
            }
            if param.get("required"):
                required.append(param_name)

        # Add body parameter if present
        if self._parameters.get("body"):
            body_info = self._parameters["body"]
            properties["body"] = {
                "type": "object",
                "description": body_info.get("description", "Request body"),
            }
            if body_info.get("required"):
                required.append("body")

        return {
            "name": self._name,
            "description": self._description or f"{self._method} {self._path}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the API call.

        Args:
            **kwargs: Tool parameters (path params, query params, body)

        Returns:
            API response as dictionary

        Raises:
            ValueError: If required parameters are missing
            OpenAPIConnectionError: If API call fails
        """
        # Build URL with path parameters
        url = self._base_url + self._path
        for param in self._parameters.get("path", []):
            param_name = param["name"]
            if param_name in kwargs:
                url = url.replace(f"{{{param_name}}}", str(kwargs[param_name]))
            elif param.get("required", True):
                raise ValueError(f"Missing required path parameter: {param_name}")

        # Build query parameters
        query_params: dict[str, Any] = {}
        for param in self._parameters.get("query", []):
            param_name = param["name"]
            if param_name in kwargs:
                query_params[param_name] = kwargs[param_name]
            elif param.get("required"):
                raise ValueError(f"Missing required query parameter: {param_name}")

        # Get request body
        body = kwargs.get("body") if self._parameters.get("body") else None

        try:
            client = await self._http_client_getter()
            response = await client.request(
                method=self._method,
                url=url,
                params=query_params if query_params else None,
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


class OpenAPIToolGenerator:
    """Generates tools dynamically from an OpenAPI specification.

    Fetches an OpenAPI JSON spec from a URL, parses it, and generates
    tool definitions that can be used by the agent.

    Args:
        spec_url: URL to fetch the OpenAPI JSON specification from
        base_url: Base URL for API calls (optional, derived from spec if not provided)
        tool_prefix: Prefix for generated tool names (e.g., "deadlock_api")
        sse_callback: Callback function to emit SSE events
        timeout: Timeout in seconds for API calls (default: 60.0)
        excluded_operations: Set of operation IDs to exclude from tool generation
    """

    def __init__(
        self,
        spec_url: str,
        sse_callback: SSECallback,
        tool_prefix: str = "",
        base_url: str | None = None,
        timeout: float = 60.0,
        excluded_operations: set[str] | None = None,
    ) -> None:
        self._spec_url = spec_url
        self._sse_callback = sse_callback
        self._tool_prefix = tool_prefix
        self._base_url = base_url
        self._timeout = timeout
        self._excluded_operations = excluded_operations or set()
        self._spec: dict[str, Any] | None = None
        self._tools: dict[str, OpenAPITool] = {}
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    @retry(max_attempts=3, base_delay=1.0)
    async def fetch_spec(self) -> dict[str, Any]:
        """Fetch and parse the OpenAPI specification from the URL.

        Returns:
            Parsed OpenAPI specification as a dictionary

        Raises:
            OpenAPIConnectionError: If unable to fetch or parse the spec
        """
        if self._spec is not None:
            return self._spec

        try:
            client = await self._get_client()
            response = await client.get(self._spec_url)
            response.raise_for_status()
            self._spec = response.json()

            # Derive base URL from spec if not provided
            if self._base_url is None and self._spec:
                servers = self._spec.get("servers", [])
                if servers:
                    self._base_url = servers[0].get("url", "")

            return self._spec
        except httpx.HTTPStatusError as e:
            raise OpenAPIConnectionError(f"Failed to fetch OpenAPI spec: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise OpenAPIConnectionError(f"Network error fetching OpenAPI spec: {e}") from e
        except Exception as e:
            raise OpenAPIConnectionError(f"Error parsing OpenAPI spec: {e}") from e

    # Maximum length for tool names
    # API limit is 64 chars, but the SDK adds "mcp__tools__" prefix (12 chars)
    # So base tool names must be at most 64 - 12 = 52 characters
    MAX_TOOL_NAME_LENGTH = 52

    def _generate_tool_name(self, path: str, method: str, operation: dict[str, Any]) -> str:
        """Generate a tool name from path, method, and operation.

        Uses operationId if available, otherwise generates from path/method.
        Tool names are truncated to 64 characters to comply with API limits.
        """
        # Prefer operationId if available
        operation_id = operation.get("operationId")
        if operation_id:
            # Convert to snake_case and add prefix
            name = operation_id.replace("-", "_").replace(".", "_")
        else:
            # Generate from path and method
            # /v1/matches/{match_id} -> matches_match_id
            path_parts = [p for p in path.split("/") if p and not p.startswith("{")]
            name = f"{method}_{('_'.join(path_parts))}"

        # Add prefix if specified
        if self._tool_prefix:
            name = f"{self._tool_prefix}_{name}"

        name = name.lower()

        # Truncate to max length if needed
        if len(name) > self.MAX_TOOL_NAME_LENGTH:
            name = name[: self.MAX_TOOL_NAME_LENGTH]

        return name

    def _parse_parameters(
        self, operation: dict[str, Any], path_params: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Parse parameters from an OpenAPI operation.

        Args:
            operation: OpenAPI operation object
            path_params: Path-level parameters to merge with operation parameters

        Returns:
            Dictionary with parameter definitions grouped by location
        """
        params: dict[str, Any] = {
            "path": [],
            "query": [],
            "header": [],
            "body": None,
        }

        # Merge path-level and operation-level parameters
        all_params = list(path_params or []) + list(operation.get("parameters", []))

        for param in all_params:
            location = param.get("in", "query")
            if location in params and isinstance(params[location], list):
                params[location].append(
                    {
                        "name": param.get("name"),
                        "required": param.get("required", False),
                        "schema": param.get("schema", {}),
                        "description": param.get("description", ""),
                    }
                )

        # Handle request body
        request_body = operation.get("requestBody")
        if request_body:
            content = request_body.get("content", {})
            # Prefer application/json
            json_content = content.get("application/json", {})
            params["body"] = {
                "required": request_body.get("required", False),
                "schema": json_content.get("schema", {}),
                "description": request_body.get("description", ""),
            }

        return params

    async def generate_tools(self) -> dict[str, OpenAPITool]:
        """Generate tool instances from the OpenAPI spec.

        Returns:
            Dictionary mapping tool names to OpenAPITool instances
        """
        if self._tools:
            return self._tools

        spec = await self.fetch_spec()
        paths = spec.get("paths", {})

        for path, path_item in paths.items():
            # Get path-level parameters
            path_params = path_item.get("parameters", [])

            for method in ["get", "post", "put", "delete", "patch"]:
                if method not in path_item:
                    continue

                operation = path_item[method]

                # Skip excluded operations
                operation_id = operation.get("operationId", "")
                if operation_id in self._excluded_operations:
                    continue

                tool_name = self._generate_tool_name(path, method, operation)
                parameters = self._parse_parameters(operation, path_params)

                tool = OpenAPITool(
                    name=tool_name,
                    path=path,
                    method=method.upper(),
                    base_url=self._base_url or "",
                    parameters=parameters,
                    description=operation.get("summary", operation.get("description", "")),
                    sse_callback=self._sse_callback,
                    timeout=self._timeout,
                    http_client_getter=self._get_client,
                )
                self._tools[tool_name] = tool

        return self._tools

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions suitable for agent configuration.

        Returns:
            List of tool definition dictionaries with name, description, and parameters
        """
        definitions = []
        for tool in self._tools.values():
            definitions.append(tool.get_definition())
        return definitions

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


__all__ = [
    "OpenAPIConnectionError",
    "OpenAPIToolGenerator",
    "OpenAPITool",
]
