# Deadlock AI Assistant - OpenAPI Tools Unit Tests

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from packages.api.models import ChatToolEndEvent, ChatToolStartEvent
from packages.tools.openapi import OpenAPIConnectionError, OpenAPITool, OpenAPIToolGenerator
from packages.tools.openapi.assets_api import (
    ASSETS_API_BASE_URL,
    GetHeroMappingTool,
    GetHeroNameTool,
    GetItemMappingTool,
    GetItemNameTool,
)
from packages.tools.openapi.deadlock_api import (
    DEADLOCK_API_BASE_URL,
    DEADLOCK_API_INFO,
    OPENAPI_SPEC_URLS,
    VALID_HTTP_METHODS,
    DeadlockAPICallTool,
    DeadlockAPIInfoTool,
    DeadlockAPISchemaFetchTool,
    DeadlockAPIToolGenerator,
)

# Sample OpenAPI spec for testing
SAMPLE_OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/v1/items": {
            "get": {
                "operationId": "list_items",
                "summary": "List all items",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer"},
                        "description": "Maximum number of items to return",
                    }
                ],
            },
        },
        "/v1/items/{item_id}": {
            "parameters": [
                {
                    "name": "item_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                    "description": "Item ID",
                }
            ],
            "get": {
                "operationId": "get_item",
                "summary": "Get item by ID",
            },
            "put": {
                "operationId": "update_item",
                "summary": "Update an item",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                        }
                    },
                    "description": "Item data to update",
                },
            },
        },
    },
}


class TestOpenAPIToolGenerator:
    """Tests for OpenAPIToolGenerator."""

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
    def generator(self, sse_callback: Any) -> OpenAPIToolGenerator:
        """Create OpenAPIToolGenerator instance."""
        return OpenAPIToolGenerator(
            spec_url="https://api.example.com/openapi.json",
            sse_callback=sse_callback,
            tool_prefix="test",
            timeout=10.0,
        )

    @pytest.mark.asyncio
    async def test_fetch_spec_success(self, generator: OpenAPIToolGenerator) -> None:
        """Test successful spec fetching."""
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_OPENAPI_SPEC
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(generator, "_get_client", return_value=mock_client):
            spec = await generator.fetch_spec()

        assert spec == SAMPLE_OPENAPI_SPEC
        mock_client.get.assert_called_once_with("https://api.example.com/openapi.json")

    @pytest.mark.asyncio
    async def test_fetch_spec_caches_result(self, generator: OpenAPIToolGenerator) -> None:
        """Test spec is cached after first fetch."""
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_OPENAPI_SPEC
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(generator, "_get_client", return_value=mock_client):
            # Fetch twice
            spec1 = await generator.fetch_spec()
            spec2 = await generator.fetch_spec()

        assert spec1 == spec2
        # Should only call once due to caching
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_spec_derives_base_url_from_spec(self, sse_callback: Any) -> None:
        """Test base URL is derived from spec servers when not provided."""
        generator = OpenAPIToolGenerator(
            spec_url="https://api.example.com/openapi.json",
            sse_callback=sse_callback,
            tool_prefix="test",
            base_url=None,  # Let it be derived from spec
        )

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_OPENAPI_SPEC
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(generator, "_get_client", return_value=mock_client):
            await generator.fetch_spec()

        assert generator._base_url == "https://api.example.com"

    @pytest.mark.asyncio
    async def test_fetch_spec_http_error(self, generator: OpenAPIToolGenerator) -> None:
        """Test handling of HTTP errors during spec fetch."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)

        with (
            patch.object(generator, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await generator.fetch_spec()

        assert "HTTP 404" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_spec_network_error(self, generator: OpenAPIToolGenerator) -> None:
        """Test handling of network errors during spec fetch."""
        error = httpx.RequestError("Connection refused")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)

        with (
            patch.object(generator, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await generator.fetch_spec()

        assert "Network error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_spec_parse_error(self, generator: OpenAPIToolGenerator) -> None:
        """Test handling of JSON parse errors."""
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with (
            patch.object(generator, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await generator.fetch_spec()

        assert "Error parsing" in str(exc_info.value)


class TestOpenAPIToolGeneratorToolGeneration:
    """Tests for OpenAPIToolGenerator tool generation."""

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
    def generator(self, sse_callback: Any) -> OpenAPIToolGenerator:
        """Create OpenAPIToolGenerator instance with pre-loaded spec."""
        gen = OpenAPIToolGenerator(
            spec_url="https://api.example.com/openapi.json",
            sse_callback=sse_callback,
            tool_prefix="test",
            base_url="https://api.example.com",
        )
        gen._spec = SAMPLE_OPENAPI_SPEC
        return gen

    @pytest.mark.asyncio
    async def test_generate_tools_creates_tools_for_each_operation(self, generator: OpenAPIToolGenerator) -> None:
        """Test tool generation creates one tool per operation."""
        tools = await generator.generate_tools()

        assert len(tools) == 3  # list_items, get_item, update_item
        assert "test_list_items" in tools
        assert "test_get_item" in tools
        assert "test_update_item" in tools

    @pytest.mark.asyncio
    async def test_generate_tools_excludes_specified_operations(self, sse_callback: Any) -> None:
        """Test tool generation excludes operations in excluded_operations set."""
        gen = OpenAPIToolGenerator(
            spec_url="https://api.example.com/openapi.json",
            sse_callback=sse_callback,
            tool_prefix="test",
            base_url="https://api.example.com",
            excluded_operations={"get_item", "update_item"},
        )
        gen._spec = SAMPLE_OPENAPI_SPEC

        tools = await gen.generate_tools()

        assert len(tools) == 1  # Only list_items should be included
        assert "test_list_items" in tools
        assert "test_get_item" not in tools
        assert "test_update_item" not in tools

    @pytest.mark.asyncio
    async def test_generate_tools_caches_result(self, generator: OpenAPIToolGenerator) -> None:
        """Test tools are cached after first generation."""
        tools1 = await generator.generate_tools()
        tools2 = await generator.generate_tools()

        assert tools1 is tools2

    @pytest.mark.asyncio
    async def test_tool_name_uses_operation_id(self, generator: OpenAPIToolGenerator) -> None:
        """Test tool names are derived from operationId."""
        tools = await generator.generate_tools()

        # operationId "list_items" becomes "test_list_items" with prefix
        assert "test_list_items" in tools
        assert "test_get_item" in tools

    def test_tool_name_truncated_to_max_length(self, generator: OpenAPIToolGenerator) -> None:
        """Test tool names are truncated to max length (52 chars to allow for MCP prefix)."""
        # Create a very long operationId
        long_operation = {"operationId": "this_is_a_very_long_operation_id_that_exceeds_the_maximum_allowed_length"}
        name = generator._generate_tool_name("/test", "get", long_operation)
        # Max is 52 chars to allow 12-char "mcp__tools__" prefix (total 64)
        assert len(name) <= 52

    def test_tool_name_not_truncated_when_short(self, generator: OpenAPIToolGenerator) -> None:
        """Test short tool names are not modified."""
        operation = {"operationId": "short_name"}
        name = generator._generate_tool_name("/test", "get", operation)
        assert name == "test_short_name"
        assert len(name) < 64

    @pytest.mark.asyncio
    async def test_tool_includes_path_parameters(self, generator: OpenAPIToolGenerator) -> None:
        """Test tool definition includes path parameters."""
        tools = await generator.generate_tools()
        tool = tools["test_get_item"]
        definition = tool.get_definition()

        assert "item_id" in definition["parameters"]["properties"]
        assert "item_id" in definition["parameters"]["required"]

    @pytest.mark.asyncio
    async def test_tool_includes_query_parameters(self, generator: OpenAPIToolGenerator) -> None:
        """Test tool definition includes query parameters."""
        tools = await generator.generate_tools()
        tool = tools["test_list_items"]
        definition = tool.get_definition()

        assert "limit" in definition["parameters"]["properties"]
        # limit is not required, so should not be in required list
        assert "limit" not in definition["parameters"].get("required", [])

    @pytest.mark.asyncio
    async def test_tool_includes_request_body(self, generator: OpenAPIToolGenerator) -> None:
        """Test tool definition includes request body when present."""
        tools = await generator.generate_tools()
        tool = tools["test_update_item"]
        definition = tool.get_definition()

        assert "body" in definition["parameters"]["properties"]
        assert "body" in definition["parameters"]["required"]

    def test_get_tool_definitions(self, generator: OpenAPIToolGenerator) -> None:
        """Test get_tool_definitions returns list of definitions."""
        # Pre-populate tools
        mock_tool = MagicMock()
        mock_tool.get_definition.return_value = {"name": "test_tool", "description": "Test"}
        generator._tools = {"test_tool": mock_tool}

        definitions = generator.get_tool_definitions()

        assert len(definitions) == 1
        assert definitions[0]["name"] == "test_tool"


class TestOpenAPITool:
    """Tests for OpenAPITool execution."""

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
    def http_client_getter(self) -> Any:
        """Create mock HTTP client getter."""

        async def getter() -> AsyncMock:
            return AsyncMock()

        return getter

    @pytest.fixture
    def tool(self, sse_callback: Any, http_client_getter: Any) -> OpenAPITool:
        """Create OpenAPITool instance."""
        return OpenAPITool(
            name="test_get_item",
            path="/v1/items/{item_id}",
            method="GET",
            base_url="https://api.example.com",
            parameters={
                "path": [
                    {"name": "item_id", "required": True, "schema": {"type": "integer"}, "description": "Item ID"}
                ],
                "query": [
                    {
                        "name": "fields",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": "Fields to return",
                    }
                ],
                "body": None,
            },
            description="Get item by ID",
            sse_callback=sse_callback,
            timeout=10.0,
            http_client_getter=http_client_getter,
        )

    @pytest.mark.asyncio
    async def test_run_builds_url_with_path_params(self, sse_callback: Any) -> None:
        """Test URL is built correctly with path parameters."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 123, "name": "Test Item"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        async def client_getter() -> AsyncMock:
            return mock_client

        tool = OpenAPITool(
            name="test_get_item",
            path="/v1/items/{item_id}",
            method="GET",
            base_url="https://api.example.com",
            parameters={
                "path": [{"name": "item_id", "required": True, "schema": {"type": "integer"}, "description": ""}],
                "query": [],
                "body": None,
            },
            description="Get item",
            sse_callback=sse_callback,
            timeout=10.0,
            http_client_getter=client_getter,
        )

        await tool._run(item_id=123)

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["url"] == "https://api.example.com/v1/items/123"

    @pytest.mark.asyncio
    async def test_run_includes_query_params(self, sse_callback: Any) -> None:
        """Test query parameters are included in request."""
        mock_response = MagicMock()
        mock_response.json.return_value = [{"id": 1}, {"id": 2}]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        async def client_getter() -> AsyncMock:
            return mock_client

        tool = OpenAPITool(
            name="test_list_items",
            path="/v1/items",
            method="GET",
            base_url="https://api.example.com",
            parameters={
                "path": [],
                "query": [{"name": "limit", "required": False, "schema": {"type": "integer"}, "description": ""}],
                "body": None,
            },
            description="List items",
            sse_callback=sse_callback,
            timeout=10.0,
            http_client_getter=client_getter,
        )

        await tool._run(limit=10)

        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["params"] == {"limit": 10}

    @pytest.mark.asyncio
    async def test_run_includes_request_body(self, sse_callback: Any) -> None:
        """Test request body is included in request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 123, "name": "Updated"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        async def client_getter() -> AsyncMock:
            return mock_client

        tool = OpenAPITool(
            name="test_update_item",
            path="/v1/items/{item_id}",
            method="PUT",
            base_url="https://api.example.com",
            parameters={
                "path": [{"name": "item_id", "required": True, "schema": {"type": "integer"}, "description": ""}],
                "query": [],
                "body": {"required": True, "schema": {}, "description": ""},
            },
            description="Update item",
            sse_callback=sse_callback,
            timeout=10.0,
            http_client_getter=client_getter,
        )

        body_data = {"name": "Updated Item"}
        await tool._run(item_id=123, body=body_data)

        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["json"] == body_data

    @pytest.mark.asyncio
    async def test_run_raises_for_missing_required_path_param(self, tool: OpenAPITool) -> None:
        """Test ValueError raised for missing required path parameter."""
        with pytest.raises(ValueError) as exc_info:
            await tool._run()  # Missing item_id

        assert "Missing required path parameter" in str(exc_info.value)
        assert "item_id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_http_error(self, sse_callback: Any) -> None:
        """Test handling of HTTP status errors."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        error = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=error)

        async def client_getter() -> AsyncMock:
            return mock_client

        tool = OpenAPITool(
            name="test_get_item",
            path="/v1/items/{item_id}",
            method="GET",
            base_url="https://api.example.com",
            parameters={
                "path": [{"name": "item_id", "required": True, "schema": {}, "description": ""}],
                "query": [],
                "body": None,
            },
            description="Get item",
            sse_callback=sse_callback,
            timeout=10.0,
            http_client_getter=client_getter,
        )

        with pytest.raises(OpenAPIConnectionError) as exc_info:
            await tool._run(item_id=123)

        assert "HTTP 404" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_network_error(self, sse_callback: Any) -> None:
        """Test handling of network errors."""
        error = httpx.RequestError("Connection refused")

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=error)

        async def client_getter() -> AsyncMock:
            return mock_client

        tool = OpenAPITool(
            name="test_get_item",
            path="/v1/items/{item_id}",
            method="GET",
            base_url="https://api.example.com",
            parameters={
                "path": [{"name": "item_id", "required": True, "schema": {}, "description": ""}],
                "query": [],
                "body": None,
            },
            description="Get item",
            sse_callback=sse_callback,
            timeout=10.0,
            http_client_getter=client_getter,
        )

        with pytest.raises(OpenAPIConnectionError) as exc_info:
            await tool._run(item_id=123)

        assert "Network error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(self, sse_callback: Any, sse_events: list) -> None:
        """Test execute emits start and end SSE events."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 123}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        async def client_getter() -> AsyncMock:
            return mock_client

        tool = OpenAPITool(
            name="test_get_item",
            path="/v1/items/{item_id}",
            method="GET",
            base_url="https://api.example.com",
            parameters={
                "path": [{"name": "item_id", "required": True, "schema": {}, "description": ""}],
                "query": [],
                "body": None,
            },
            description="Get item",
            sse_callback=sse_callback,
            timeout=10.0,
            http_client_getter=client_getter,
        )

        await tool.execute(item_id=123)

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "test_get_item"
        assert start_event.arguments == {"item_id": 123}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.tool_name == "test_get_item"
        assert end_event.success is True

    def test_name_property(self, tool: OpenAPITool) -> None:
        """Test name property returns correct name."""
        assert tool.name == "test_get_item"

    def test_get_definition(self, tool: OpenAPITool) -> None:
        """Test get_definition returns correct structure."""
        definition = tool.get_definition()

        assert definition["name"] == "test_get_item"
        assert definition["description"] == "Get item by ID"
        assert "properties" in definition["parameters"]
        assert "item_id" in definition["parameters"]["properties"]

    def test_result_summary_with_error(self, tool: OpenAPITool) -> None:
        """Test result summary formatting with error."""
        summary = tool._create_result_summary({"error": "Not found"})
        assert "Error: Not found" in summary

    def test_result_summary_with_response(self, tool: OpenAPITool) -> None:
        """Test result summary formatting with response."""
        summary = tool._create_result_summary({"id": 1, "name": "Test", "type": "item"})
        assert "3 fields" in summary


class TestDeadlockAPICallTool:
    """Tests for DeadlockAPICallTool."""

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
    def api_call_tool(self, sse_callback: Any) -> DeadlockAPICallTool:
        """Create DeadlockAPICallTool instance."""
        return DeadlockAPICallTool(sse_callback=sse_callback, timeout=10.0)

    @pytest.mark.asyncio
    async def test_run_executes_get_request(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test GET request execution."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(api_call_tool, "_get_client", return_value=mock_client):
            result = await api_call_tool._run(method="GET", path="/v1/test")

        assert result == {"data": "test"}
        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["url"] == f"{DEADLOCK_API_BASE_URL}/v1/test"

    @pytest.mark.asyncio
    async def test_run_accepts_lowercase_method(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test method is converted to uppercase."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(api_call_tool, "_get_client", return_value=mock_client):
            await api_call_tool._run(method="get", path="/v1/test")

        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["method"] == "GET"

    @pytest.mark.asyncio
    async def test_run_with_query_params(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test request with query parameters."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(api_call_tool, "_get_client", return_value=mock_client):
            await api_call_tool._run(method="GET", path="/v1/test", query_params={"limit": 10})

        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["params"] == {"limit": 10}

    @pytest.mark.asyncio
    async def test_run_with_request_body(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test request with JSON body."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(api_call_tool, "_get_client", return_value=mock_client):
            await api_call_tool._run(method="POST", path="/v1/test", body={"name": "test"})

        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["json"] == {"name": "test"}

    @pytest.mark.asyncio
    async def test_run_validates_method(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test invalid method raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            await api_call_tool._run(method="INVALID", path="/v1/test")

        assert "Invalid HTTP method" in str(exc_info.value)
        assert "INVALID" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_validates_path_starts_with_slash(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test path without leading slash raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            await api_call_tool._run(method="GET", path="v1/test")

        assert "Path must start with /" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_http_error(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test HTTP error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        error = httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=error)

        with (
            patch.object(api_call_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await api_call_tool._run(method="GET", path="/v1/test")

        assert "HTTP 500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_network_error(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test network error handling."""
        error = httpx.RequestError("Connection failed")

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=error)

        with (
            patch.object(api_call_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await api_call_tool._run(method="GET", path="/v1/test")

        assert "Network error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self, api_call_tool: DeadlockAPICallTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits start and end SSE events."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(api_call_tool, "_get_client", return_value=mock_client):
            await api_call_tool.execute(method="GET", path="/v1/test")

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "deadlock_api_call"
        assert start_event.arguments["method"] == "GET"
        assert start_event.arguments["path"] == "/v1/test"

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is True

    def test_name_property(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test name property returns correct name."""
        assert api_call_tool.name == "deadlock_api_call"

    def test_get_definition(self, api_call_tool: DeadlockAPICallTool) -> None:
        """Test get_definition returns correct structure."""
        definition = api_call_tool.get_definition()

        assert definition["name"] == "deadlock_api_call"
        assert "method" in definition["parameters"]["properties"]
        assert "path" in definition["parameters"]["properties"]
        assert "query_params" in definition["parameters"]["properties"]
        assert "body" in definition["parameters"]["properties"]
        assert "method" in definition["parameters"]["required"]
        assert "path" in definition["parameters"]["required"]

    def test_valid_http_methods_constant(self) -> None:
        """Test VALID_HTTP_METHODS contains expected methods."""
        assert "GET" in VALID_HTTP_METHODS
        assert "POST" in VALID_HTTP_METHODS
        assert "PUT" in VALID_HTTP_METHODS
        assert "DELETE" in VALID_HTTP_METHODS
        assert "PATCH" in VALID_HTTP_METHODS


class TestGetHeroNameTool:
    """Tests for GetHeroNameTool."""

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
    def hero_name_tool(self, sse_callback: Any) -> GetHeroNameTool:
        """Create GetHeroNameTool instance."""
        return GetHeroNameTool(sse_callback=sse_callback, timeout=10.0)

    @pytest.mark.asyncio
    async def test_run_returns_hero_name(self, hero_name_tool: GetHeroNameTool) -> None:
        """Test successful hero name retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1, "name": "Abrams", "class_name": "hero_abrams"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(hero_name_tool, "_get_client", return_value=mock_client):
            result = await hero_name_tool._run(hero_id=1)

        assert result == "Abrams"
        mock_client.get.assert_called_once_with(f"{ASSETS_API_BASE_URL}/v2/heroes/1")

    @pytest.mark.asyncio
    async def test_run_validates_positive_integer(self, hero_name_tool: GetHeroNameTool) -> None:
        """Test hero_id must be a positive integer."""
        with pytest.raises(ValueError) as exc_info:
            await hero_name_tool._run(hero_id=0)

        assert "positive integer" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_validates_negative_integer(self, hero_name_tool: GetHeroNameTool) -> None:
        """Test negative hero_id raises error."""
        with pytest.raises(ValueError) as exc_info:
            await hero_name_tool._run(hero_id=-1)

        assert "positive integer" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_404_not_found(self, hero_name_tool: GetHeroNameTool) -> None:
        """Test handling of 404 response."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)

        with (
            patch.object(hero_name_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await hero_name_tool._run(hero_id=9999)

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_missing_name_field(self, hero_name_tool: GetHeroNameTool) -> None:
        """Test error when response lacks name field."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1}  # No name field
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with (
            patch.object(hero_name_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await hero_name_tool._run(hero_id=1)

        assert "no name field" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self, hero_name_tool: GetHeroNameTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits start and end SSE events."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1, "name": "Bebop"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(hero_name_tool, "_get_client", return_value=mock_client):
            await hero_name_tool.execute(hero_id=1)

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "get_hero_name"
        assert start_event.arguments == {"hero_id": 1}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is True
        assert "Bebop" in end_event.result_summary

    def test_name_property(self, hero_name_tool: GetHeroNameTool) -> None:
        """Test name property returns correct name."""
        assert hero_name_tool.name == "get_hero_name"

    def test_get_definition(self, hero_name_tool: GetHeroNameTool) -> None:
        """Test get_definition returns correct structure."""
        definition = hero_name_tool.get_definition()

        assert definition["name"] == "get_hero_name"
        assert "hero_id" in definition["parameters"]["properties"]
        assert "hero_id" in definition["parameters"]["required"]
        assert definition["parameters"]["properties"]["hero_id"]["type"] == "integer"

    def test_result_summary_formatting(self, hero_name_tool: GetHeroNameTool) -> None:
        """Test result summary formatting."""
        summary = hero_name_tool._create_result_summary("Abrams")
        assert "Hero name: Abrams" in summary


class TestGetItemNameTool:
    """Tests for GetItemNameTool."""

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
    def item_name_tool(self, sse_callback: Any) -> GetItemNameTool:
        """Create GetItemNameTool instance."""
        return GetItemNameTool(sse_callback=sse_callback, timeout=10.0)

    @pytest.mark.asyncio
    async def test_run_returns_item_name(self, item_name_tool: GetItemNameTool) -> None:
        """Test successful item name retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 100, "name": "Basic Magazine", "class_name": "upgrade_magazine"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(item_name_tool, "_get_client", return_value=mock_client):
            result = await item_name_tool._run(item_id=100)

        assert result == "Basic Magazine"
        mock_client.get.assert_called_once_with(f"{ASSETS_API_BASE_URL}/v2/items/100")

    @pytest.mark.asyncio
    async def test_run_validates_positive_integer(self, item_name_tool: GetItemNameTool) -> None:
        """Test item_id must be a positive integer."""
        with pytest.raises(ValueError) as exc_info:
            await item_name_tool._run(item_id=0)

        assert "positive integer" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_validates_negative_integer(self, item_name_tool: GetItemNameTool) -> None:
        """Test negative item_id raises error."""
        with pytest.raises(ValueError) as exc_info:
            await item_name_tool._run(item_id=-1)

        assert "positive integer" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_404_not_found(self, item_name_tool: GetItemNameTool) -> None:
        """Test handling of 404 response."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)

        with (
            patch.object(item_name_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await item_name_tool._run(item_id=9999)

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_missing_name_field(self, item_name_tool: GetItemNameTool) -> None:
        """Test error when response lacks name field."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 100}  # No name field
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with (
            patch.object(item_name_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await item_name_tool._run(item_id=100)

        assert "no name field" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self, item_name_tool: GetItemNameTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits start and end SSE events."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 100, "name": "Healing Rite"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(item_name_tool, "_get_client", return_value=mock_client):
            await item_name_tool.execute(item_id=100)

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "get_item_name"
        assert start_event.arguments == {"item_id": 100}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is True
        assert "Healing Rite" in end_event.result_summary

    def test_name_property(self, item_name_tool: GetItemNameTool) -> None:
        """Test name property returns correct name."""
        assert item_name_tool.name == "get_item_name"

    def test_get_definition(self, item_name_tool: GetItemNameTool) -> None:
        """Test get_definition returns correct structure."""
        definition = item_name_tool.get_definition()

        assert definition["name"] == "get_item_name"
        assert "item_id" in definition["parameters"]["properties"]
        assert "item_id" in definition["parameters"]["required"]
        assert definition["parameters"]["properties"]["item_id"]["type"] == "integer"

    def test_result_summary_formatting(self, item_name_tool: GetItemNameTool) -> None:
        """Test result summary formatting."""
        summary = item_name_tool._create_result_summary("Basic Magazine")
        assert "Item name: Basic Magazine" in summary


class TestGetHeroMappingTool:
    """Tests for GetHeroMappingTool."""

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
    def hero_mapping_tool(self, sse_callback: Any) -> GetHeroMappingTool:
        """Create GetHeroMappingTool instance."""
        return GetHeroMappingTool(sse_callback=sse_callback, timeout=10.0)

    @pytest.mark.asyncio
    async def test_run_returns_hero_mapping(self, hero_mapping_tool: GetHeroMappingTool) -> None:
        """Test successful hero mapping retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"id": 1, "name": "Abrams", "class_name": "hero_abrams"},
            {"id": 2, "name": "Bebop", "class_name": "hero_bebop"},
            {"id": 3, "name": "Dynamo", "class_name": "hero_dynamo"},
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(hero_mapping_tool, "_get_client", return_value=mock_client):
            result = await hero_mapping_tool._run()

        assert result == {"Abrams": 1, "Bebop": 2, "Dynamo": 3}
        mock_client.get.assert_called_once_with(f"{ASSETS_API_BASE_URL}/v2/heroes")

    @pytest.mark.asyncio
    async def test_run_skips_entries_without_id_or_name(self, hero_mapping_tool: GetHeroMappingTool) -> None:
        """Test entries without id or name are skipped."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"id": 1, "name": "Abrams"},
            {"id": 2},  # No name
            {"name": "Bebop"},  # No id
            {"id": 3, "name": "Dynamo"},
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(hero_mapping_tool, "_get_client", return_value=mock_client):
            result = await hero_mapping_tool._run()

        assert result == {"Abrams": 1, "Dynamo": 3}

    @pytest.mark.asyncio
    async def test_run_handles_http_error(self, hero_mapping_tool: GetHeroMappingTool) -> None:
        """Test handling of HTTP error response."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        error = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)

        with (
            patch.object(hero_mapping_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await hero_mapping_tool._run()

        assert "HTTP 500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self, hero_mapping_tool: GetHeroMappingTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits start and end SSE events."""
        mock_response = MagicMock()
        mock_response.json.return_value = [{"id": 1, "name": "Abrams"}]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(hero_mapping_tool, "_get_client", return_value=mock_client):
            await hero_mapping_tool.execute()

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "get_hero_mapping"

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is True
        assert "1 heroes" in end_event.result_summary

    def test_name_property(self, hero_mapping_tool: GetHeroMappingTool) -> None:
        """Test name property returns correct name."""
        assert hero_mapping_tool.name == "get_hero_mapping"

    def test_get_definition(self, hero_mapping_tool: GetHeroMappingTool) -> None:
        """Test get_definition returns correct structure."""
        definition = hero_mapping_tool.get_definition()

        assert definition["name"] == "get_hero_mapping"
        assert definition["parameters"]["properties"] == {}
        assert definition["parameters"]["required"] == []

    def test_result_summary_formatting(self, hero_mapping_tool: GetHeroMappingTool) -> None:
        """Test result summary formatting."""
        result = {"Abrams": 1, "Bebop": 2}
        summary = hero_mapping_tool._create_result_summary(result)
        assert "Hero mapping: 2 heroes" in summary


class TestGetItemMappingTool:
    """Tests for GetItemMappingTool."""

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
    def item_mapping_tool(self, sse_callback: Any) -> GetItemMappingTool:
        """Create GetItemMappingTool instance."""
        return GetItemMappingTool(sse_callback=sse_callback, timeout=10.0)

    @pytest.mark.asyncio
    async def test_run_returns_item_mapping(self, item_mapping_tool: GetItemMappingTool) -> None:
        """Test successful item mapping retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"id": 100, "name": "Basic Magazine", "class_name": "upgrade_magazine"},
            {"id": 101, "name": "Healing Rite", "class_name": "ability_healing_rite"},
            {"id": 102, "name": "Monster Rounds", "class_name": "upgrade_monster_rounds"},
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(item_mapping_tool, "_get_client", return_value=mock_client):
            result = await item_mapping_tool._run()

        assert result == {"Basic Magazine": 100, "Healing Rite": 101, "Monster Rounds": 102}
        mock_client.get.assert_called_once_with(f"{ASSETS_API_BASE_URL}/v2/items")

    @pytest.mark.asyncio
    async def test_run_skips_entries_without_id_or_name(self, item_mapping_tool: GetItemMappingTool) -> None:
        """Test entries without id or name are skipped."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"id": 100, "name": "Basic Magazine"},
            {"id": 101},  # No name
            {"name": "Healing Rite"},  # No id
            {"id": 102, "name": "Monster Rounds"},
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(item_mapping_tool, "_get_client", return_value=mock_client):
            result = await item_mapping_tool._run()

        assert result == {"Basic Magazine": 100, "Monster Rounds": 102}

    @pytest.mark.asyncio
    async def test_run_handles_http_error(self, item_mapping_tool: GetItemMappingTool) -> None:
        """Test handling of HTTP error response."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        error = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)

        with (
            patch.object(item_mapping_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await item_mapping_tool._run()

        assert "HTTP 500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self, item_mapping_tool: GetItemMappingTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits start and end SSE events."""
        mock_response = MagicMock()
        mock_response.json.return_value = [{"id": 100, "name": "Basic Magazine"}]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(item_mapping_tool, "_get_client", return_value=mock_client):
            await item_mapping_tool.execute()

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "get_item_mapping"

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is True
        assert "1 items" in end_event.result_summary

    def test_name_property(self, item_mapping_tool: GetItemMappingTool) -> None:
        """Test name property returns correct name."""
        assert item_mapping_tool.name == "get_item_mapping"

    def test_get_definition(self, item_mapping_tool: GetItemMappingTool) -> None:
        """Test get_definition returns correct structure."""
        definition = item_mapping_tool.get_definition()

        assert definition["name"] == "get_item_mapping"
        assert definition["parameters"]["properties"] == {}
        assert definition["parameters"]["required"] == []

    def test_result_summary_formatting(self, item_mapping_tool: GetItemMappingTool) -> None:
        """Test result summary formatting."""
        result = {"Basic Magazine": 100, "Healing Rite": 101}
        summary = item_mapping_tool._create_result_summary(result)
        assert "Item mapping: 2 items" in summary


class TestDeadlockAPIInfoTool:
    """Tests for DeadlockAPIInfoTool."""

    @pytest.fixture
    def sse_callback(self) -> Any:
        """Create SSE callback."""

        def callback(event: ChatToolStartEvent | ChatToolEndEvent) -> None:
            pass  # noqa: ARG001

        return callback

    @pytest.fixture
    def info_tool(self, sse_callback: Any) -> DeadlockAPIInfoTool:
        """Create DeadlockAPIInfoTool instance."""
        return DeadlockAPIInfoTool(sse_callback=sse_callback, timeout=10.0)

    def test_name_property(self, info_tool: DeadlockAPIInfoTool) -> None:
        """Test name property returns correct name."""
        assert info_tool.name == "deadlock_api_info"

    def test_get_definition(self, info_tool: DeadlockAPIInfoTool) -> None:
        """Test get_definition returns correct structure."""
        definition = info_tool.get_definition()
        assert definition["name"] == "deadlock_api_info"
        assert "Deadlock API" in definition["description"]

    @pytest.mark.asyncio
    async def test_run_returns_api_info(self, info_tool: DeadlockAPIInfoTool) -> None:
        """Test _run returns the DEADLOCK_API_INFO dictionary."""
        result = await info_tool._run()
        assert result == DEADLOCK_API_INFO

    def test_api_info_contains_overview(self) -> None:
        """Test DEADLOCK_API_INFO includes an overview section."""
        assert "overview" in DEADLOCK_API_INFO
        assert "description" in DEADLOCK_API_INFO["overview"]

    def test_api_info_contains_data_api_with_docs(self) -> None:
        """Test DEADLOCK_API_INFO includes data API with docs link."""
        assert "data_api" in DEADLOCK_API_INFO
        data_api = DEADLOCK_API_INFO["data_api"]
        assert "url" in data_api
        assert "openapi_spec" in data_api
        assert "docs" in data_api

    def test_api_info_contains_assets_api_with_docs(self) -> None:
        """Test DEADLOCK_API_INFO includes assets API with docs link."""
        assert "assets_api" in DEADLOCK_API_INFO
        assets_api = DEADLOCK_API_INFO["assets_api"]
        assert "url" in assets_api
        assert "openapi_spec" in assets_api
        assert "docs" in assets_api

    def test_api_info_contains_community_links(self) -> None:
        """Test DEADLOCK_API_INFO includes GitHub, Discord, and Patreon links."""
        assert "github" in DEADLOCK_API_INFO
        assert "discord" in DEADLOCK_API_INFO
        assert "patreon" in DEADLOCK_API_INFO

    def test_result_summary(self, info_tool: DeadlockAPIInfoTool) -> None:
        """Test result summary formatting."""
        summary = info_tool._create_result_summary(DEADLOCK_API_INFO)
        assert f"{len(DEADLOCK_API_INFO)}" in summary


class TestDeadlockAPISchemaFetchTool:
    """Tests for DeadlockAPISchemaFetchTool."""

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
    def schema_tool(self, sse_callback: Any) -> DeadlockAPISchemaFetchTool:
        """Create DeadlockAPISchemaFetchTool instance."""
        return DeadlockAPISchemaFetchTool(sse_callback=sse_callback, timeout=10.0)

    def test_name_property(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test name property returns correct name."""
        assert schema_tool.name == "deadlock_api_schema"

    def test_get_definition(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test get_definition returns correct structure."""
        definition = schema_tool.get_definition()
        assert definition["name"] == "deadlock_api_schema"
        assert "api" in definition["parameters"]["properties"]
        assert "api" in definition["parameters"]["required"]
        assert definition["parameters"]["properties"]["api"]["enum"] == ["data", "assets"]

    @pytest.mark.asyncio
    async def test_run_fetches_data_api_schema(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test fetching the data API schema."""
        sample_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Deadlock API", "version": "1.0.0"},
            "paths": {"/v1/matches": {"get": {"summary": "Get matches"}}},
        }

        mock_response = MagicMock()
        mock_response.json.return_value = sample_spec
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(schema_tool, "_get_client", return_value=mock_client):
            result = await schema_tool._run(api="data")

        assert result == sample_spec
        mock_client.get.assert_called_once_with(OPENAPI_SPEC_URLS["data"])

    @pytest.mark.asyncio
    async def test_run_fetches_assets_api_schema(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test fetching the assets API schema."""
        sample_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Deadlock Assets API", "version": "1.0.0"},
            "paths": {"/v2/heroes": {"get": {"summary": "Get heroes"}}},
        }

        mock_response = MagicMock()
        mock_response.json.return_value = sample_spec
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(schema_tool, "_get_client", return_value=mock_client):
            result = await schema_tool._run(api="assets")

        assert result == sample_spec
        mock_client.get.assert_called_once_with(OPENAPI_SPEC_URLS["assets"])

    @pytest.mark.asyncio
    async def test_run_rejects_invalid_api_name(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test ValueError raised for invalid API name."""
        with pytest.raises(ValueError) as exc_info:
            await schema_tool._run(api="invalid")

        assert "Invalid API name" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_http_error(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test handling of HTTP errors during schema fetch."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        error = httpx.HTTPStatusError("Service Unavailable", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)

        with (
            patch.object(schema_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await schema_tool._run(api="data")

        assert "HTTP 503" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_handles_network_error(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test handling of network errors during schema fetch."""
        error = httpx.RequestError("Connection refused")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)

        with (
            patch.object(schema_tool, "_get_client", return_value=mock_client),
            pytest.raises(OpenAPIConnectionError) as exc_info,
        ):
            await schema_tool._run(api="assets")

        assert "Network error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self, schema_tool: DeadlockAPISchemaFetchTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits start and end SSE events."""
        sample_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Deadlock API", "version": "1.0.0"},
            "paths": {"/v1/matches": {}, "/v1/players": {}},
        }

        mock_response = MagicMock()
        mock_response.json.return_value = sample_spec
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(schema_tool, "_get_client", return_value=mock_client):
            await schema_tool.execute(api="data")

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "deadlock_api_schema"
        assert start_event.arguments == {"api": "data"}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is True
        assert "2 endpoints" in end_event.result_summary

    def test_result_summary_formatting(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test result summary formatting."""
        result = {
            "info": {"title": "Deadlock API"},
            "paths": {"/v1/matches": {}, "/v1/players": {}, "/v1/heroes": {}},
        }
        summary = schema_tool._create_result_summary(result)
        assert "Deadlock API" in summary
        assert "3 endpoints" in summary

    def test_result_summary_with_missing_info(self, schema_tool: DeadlockAPISchemaFetchTool) -> None:
        """Test result summary with missing info section."""
        result = {"paths": {"/v1/matches": {}}}
        summary = schema_tool._create_result_summary(result)
        assert "Unknown API" in summary
        assert "1 endpoints" in summary

    def test_openapi_spec_urls_constant(self) -> None:
        """Test OPENAPI_SPEC_URLS contains expected entries."""
        assert "data" in OPENAPI_SPEC_URLS
        assert "assets" in OPENAPI_SPEC_URLS
        assert OPENAPI_SPEC_URLS["data"] == "https://api.deadlock-api.com/openapi.json"
        assert OPENAPI_SPEC_URLS["assets"] == "https://assets.deadlock-api.com/openapi.json"


class TestDeadlockAPIToolGenerator:
    """Tests for DeadlockAPIToolGenerator."""

    @pytest.fixture
    def sse_callback(self) -> Any:
        """Create SSE callback."""

        def callback(event: ChatToolStartEvent | ChatToolEndEvent) -> None:
            pass  # noqa: ARG001

        return callback

    def test_uses_correct_spec_url(self, sse_callback: Any) -> None:
        """Test generator uses Deadlock API spec URL."""
        generator = DeadlockAPIToolGenerator(sse_callback=sse_callback)
        assert generator._spec_url == "https://api.deadlock-api.com/openapi.json"

    def test_uses_correct_tool_prefix(self, sse_callback: Any) -> None:
        """Test generator uses dl prefix."""
        generator = DeadlockAPIToolGenerator(sse_callback=sse_callback)
        assert generator._tool_prefix == "dl"

    def test_excludes_sql_operation(self, sse_callback: Any) -> None:
        """Test generator excludes SQL operation to use ClickHouse tool instead."""
        generator = DeadlockAPIToolGenerator(sse_callback=sse_callback)
        assert "sql" in generator._excluded_operations


class TestOpenAPIToolsIntegration:
    """Integration tests for OpenAPI tools."""

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
    async def test_generate_and_execute_tool(self, sse_callback: Any, sse_events: list) -> None:
        """Test complete workflow: generate tools then execute one."""
        # Create generator with pre-loaded spec
        generator = OpenAPIToolGenerator(
            spec_url="https://api.example.com/openapi.json",
            sse_callback=sse_callback,
            tool_prefix="test",
            base_url="https://api.example.com",
        )
        generator._spec = SAMPLE_OPENAPI_SPEC

        # Generate tools
        tools = await generator.generate_tools()
        assert "test_get_item" in tools

        # Mock HTTP client for tool execution
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 123, "name": "Test Item"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        # Execute tool - the tool uses the generator's _get_client via a callback
        # so we need to set the generator's _http_client directly
        tool = tools["test_get_item"]
        generator._http_client = mock_client

        result = await tool.execute(item_id=123)

        assert result["id"] == 123
        assert result["name"] == "Test Item"

        # Verify SSE events were emitted
        assert len(sse_events) == 2
        assert sse_events[0].tool_name == "test_get_item"
        assert sse_events[1].success is True
