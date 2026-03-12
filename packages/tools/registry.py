# Deadlock AI Assistant - Tool Registry
# Central registry combining all toolsets for the agent

import logging
from dataclasses import dataclass, field
from typing import Any

from packages.tools.base import BaseTool, SSECallback
from packages.tools.clickhouse import ClickHouseToolset
from packages.tools.datetime import DateTimeTool
from packages.tools.openapi import OpenAPITool
from packages.tools.openapi.assets_api import (
    AssetsAPICallTool,
    GetHeroMappingTool,
    GetHeroNameTool,
    GetItemMappingTool,
    GetItemNameTool,
)
from packages.tools.openapi.deadlock_api import (
    DeadlockAPICallTool,
    DeadlockAPIEndpointDetailsTool,
    DeadlockAPIInfoTool,
    DeadlockAPIListEndpointsTool,
)
from packages.tools.wiki import WikiGetPageTool, WikiSearchTool

logger = logging.getLogger("deadlock_assistant")


@dataclass
class ToolLoadWarning:
    """Warning about a tool or toolset that failed to load."""

    toolset: str
    message: str
    exception: Exception | None = None


@dataclass
class ToolLoadResult:
    """Result of loading tools, including any warnings."""

    tools: dict[str, BaseTool | OpenAPITool] = field(default_factory=dict)
    warnings: list[ToolLoadWarning] = field(default_factory=list)


class ToolRegistry:
    """Central registry that combines all toolsets for the agent.

    Provides lazy initialization of toolsets and unified access to all tools.
    Toolsets are only connected when their tools are first used.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Default timeout for tool executions (default: 60.0)
    """

    def __init__(self, sse_callback: SSECallback, timeout: float = 60.0) -> None:
        self._sse_callback = sse_callback
        self._timeout = timeout

        # Toolsets (lazy initialized)
        self._wiki_tools: dict[str, BaseTool] | None = None
        self._deadlock_api_call_tool: DeadlockAPICallTool | None = None
        self._deadlock_api_info_tool: DeadlockAPIInfoTool | None = None
        self._deadlock_api_list_endpoints_tool: DeadlockAPIListEndpointsTool | None = None
        self._deadlock_api_endpoint_details_tool: DeadlockAPIEndpointDetailsTool | None = None
        self._assets_api_call_tool: AssetsAPICallTool | None = None
        self._helper_tools: dict[str, BaseTool] | None = None
        self._clickhouse_toolset: ClickHouseToolset | None = None
        self._clickhouse_tools: dict[str, BaseTool] | None = None
        self._datetime_tools: dict[str, BaseTool] | None = None

        # Cache for all tools (name -> tool instance)
        self._all_tools: dict[str, BaseTool | OpenAPITool] | None = None

        # Track warnings from tool loading
        self._warnings: list[ToolLoadWarning] = []

    def _get_wiki_tools(self) -> dict[str, BaseTool]:
        """Lazy initialization of wiki tools."""
        if self._wiki_tools is None:
            self._wiki_tools = {
                "wiki_search": WikiSearchTool(self._sse_callback, self._timeout),
                "wiki_get_page": WikiGetPageTool(self._sse_callback, self._timeout),
            }
        return self._wiki_tools

    def _get_deadlock_api_call_tool(self) -> DeadlockAPICallTool:
        """Lazy initialization of generic Deadlock API call tool."""
        if self._deadlock_api_call_tool is None:
            self._deadlock_api_call_tool = DeadlockAPICallTool(
                sse_callback=self._sse_callback,
                timeout=self._timeout,
            )
        return self._deadlock_api_call_tool

    def _get_deadlock_api_info_tool(self) -> DeadlockAPIInfoTool:
        """Lazy initialization of Deadlock API info tool."""
        if self._deadlock_api_info_tool is None:
            self._deadlock_api_info_tool = DeadlockAPIInfoTool(
                sse_callback=self._sse_callback,
                timeout=self._timeout,
            )
        return self._deadlock_api_info_tool

    def _get_deadlock_api_list_endpoints_tool(self) -> DeadlockAPIListEndpointsTool:
        """Lazy initialization of Deadlock API list endpoints tool."""
        if self._deadlock_api_list_endpoints_tool is None:
            self._deadlock_api_list_endpoints_tool = DeadlockAPIListEndpointsTool(
                sse_callback=self._sse_callback,
                timeout=self._timeout,
            )
        return self._deadlock_api_list_endpoints_tool

    def _get_deadlock_api_endpoint_details_tool(self) -> DeadlockAPIEndpointDetailsTool:
        """Lazy initialization of Deadlock API endpoint details tool."""
        if self._deadlock_api_endpoint_details_tool is None:
            self._deadlock_api_endpoint_details_tool = DeadlockAPIEndpointDetailsTool(
                sse_callback=self._sse_callback,
                timeout=self._timeout,
            )
        return self._deadlock_api_endpoint_details_tool

    def _get_assets_api_call_tool(self) -> AssetsAPICallTool:
        """Lazy initialization of generic Assets API call tool."""
        if self._assets_api_call_tool is None:
            self._assets_api_call_tool = AssetsAPICallTool(
                sse_callback=self._sse_callback,
                timeout=self._timeout,
            )
        return self._assets_api_call_tool

    def _get_helper_tools(self) -> dict[str, BaseTool]:
        """Lazy initialization of helper tools (hero/item name and mapping tools)."""
        if self._helper_tools is None:
            self._helper_tools = {
                "get_hero_name": GetHeroNameTool(self._sse_callback, self._timeout),
                "get_hero_mapping": GetHeroMappingTool(self._sse_callback, self._timeout),
                "get_item_name": GetItemNameTool(self._sse_callback, self._timeout),
                "get_item_mapping": GetItemMappingTool(self._sse_callback, self._timeout),
            }
        return self._helper_tools

    def _get_clickhouse_toolset(self) -> ClickHouseToolset:
        """Lazy initialization of ClickHouse toolset."""
        if self._clickhouse_toolset is None:
            self._clickhouse_toolset = ClickHouseToolset(
                sse_callback=self._sse_callback,
                timeout=self._timeout,
            )
        return self._clickhouse_toolset

    def _get_clickhouse_tools(self) -> dict[str, BaseTool]:
        """Lazy initialization of ClickHouse tools."""
        if self._clickhouse_tools is None:
            toolset = self._get_clickhouse_toolset()
            tools = toolset.get_tools()
            self._clickhouse_tools = {tool.name: tool for tool in tools}
        return self._clickhouse_tools

    def _get_datetime_tools(self) -> dict[str, BaseTool]:
        """Lazy initialization of datetime tools."""
        if self._datetime_tools is None:
            self._datetime_tools = {
                "datetime_timestamp": DateTimeTool(self._sse_callback, self._timeout),
            }
        return self._datetime_tools

    async def get_all_tools(self) -> dict[str, BaseTool | OpenAPITool]:
        """Get all registered tools.

        Lazily initializes all toolsets and returns a dictionary mapping
        tool names to tool instances. If any toolset fails to load, a warning
        is recorded and the toolset is skipped.

        Returns:
            Dictionary mapping tool names to tool instances
        """
        if self._all_tools is None:
            self._all_tools = {}
            self._warnings = []  # Reset warnings on reload

            # Wiki tools (2 tools)
            try:
                self._all_tools.update(self._get_wiki_tools())
            except Exception as e:
                self._warnings.append(ToolLoadWarning("Wiki", str(e), e))

            # Generic Deadlock API call tool (1 tool - replaces ~50 dynamic tools)
            try:
                deadlock_api_call = self._get_deadlock_api_call_tool()
                self._all_tools[deadlock_api_call.name] = deadlock_api_call
            except Exception as e:
                self._warnings.append(ToolLoadWarning("Deadlock API Call", str(e), e))

            # Deadlock API info tool (1 tool)
            try:
                deadlock_api_info = self._get_deadlock_api_info_tool()
                self._all_tools[deadlock_api_info.name] = deadlock_api_info
            except Exception as e:
                self._warnings.append(ToolLoadWarning("Deadlock API Info", str(e), e))

            # Deadlock API list endpoints tool (1 tool)
            try:
                list_endpoints = self._get_deadlock_api_list_endpoints_tool()
                self._all_tools[list_endpoints.name] = list_endpoints
            except Exception as e:
                self._warnings.append(ToolLoadWarning("Deadlock API List Endpoints", str(e), e))

            # Deadlock API endpoint details tool (1 tool)
            try:
                endpoint_details = self._get_deadlock_api_endpoint_details_tool()
                self._all_tools[endpoint_details.name] = endpoint_details
            except Exception as e:
                self._warnings.append(ToolLoadWarning("Deadlock API Endpoint Details", str(e), e))

            # Generic Assets API call tool (1 tool - replaces ~20 dynamic tools)
            try:
                assets_api_call = self._get_assets_api_call_tool()
                self._all_tools[assets_api_call.name] = assets_api_call
            except Exception as e:
                self._warnings.append(ToolLoadWarning("Assets API Call", str(e), e))

            # Helper tools: get_hero_name, get_hero_mapping, get_item_name, get_item_mapping (4 tools)
            try:
                self._all_tools.update(self._get_helper_tools())
            except Exception as e:
                self._warnings.append(ToolLoadWarning("Helper tools", str(e), e))

            # ClickHouse tools (3 tools)
            try:
                self._all_tools.update(self._get_clickhouse_tools())
            except Exception as e:
                self._warnings.append(ToolLoadWarning("ClickHouse", str(e), e))

            # DateTime tools (1 tool)
            try:
                self._all_tools.update(self._get_datetime_tools())
            except Exception as e:
                self._warnings.append(ToolLoadWarning("DateTime", str(e), e))

            # Log results
            for warning in self._warnings:
                logger.warning(
                    "Tool loading failed",
                    extra={"toolset": warning.toolset, "error": warning.message},
                )
            logger.info(
                "Tool registry initialized",
                extra={"tool_count": len(self._all_tools), "failed_count": len(self._warnings)},
            )

        return self._all_tools

    def get_warnings(self) -> list[ToolLoadWarning]:
        """Get any warnings from tool loading.

        Returns:
            List of warnings about tools that failed to load
        """
        return self._warnings.copy()

    async def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions for agent configuration.

        Returns a list of tool definitions suitable for configuring the agent,
        each containing name, description, and parameters.

        Returns:
            List of tool definition dictionaries
        """
        tools = await self.get_all_tools()
        definitions: list[dict[str, Any]] = []

        for tool in tools.values():
            get_def = getattr(tool, "get_definition", None)
            if callable(get_def):
                definitions.append(get_def())

        return definitions

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool by name with the given arguments.

        Args:
            tool_name: Name of the tool to execute
            arguments: Dictionary of arguments to pass to the tool

        Returns:
            Tool execution result

        Raises:
            ValueError: If tool_name is not registered
            Exception: Any exception raised by the tool execution
        """
        tools = await self.get_all_tools()

        if tool_name not in tools:
            raise ValueError(f"Unknown tool: {tool_name}. Available tools: {list(tools.keys())}")

        tool = tools[tool_name]
        return await tool.execute(**arguments)

    async def close(self) -> None:
        """Close all toolset connections and clean up resources."""
        # Close generic Deadlock API call tool
        if self._deadlock_api_call_tool is not None:
            await self._deadlock_api_call_tool.close()

        # Close Deadlock API list endpoints tool
        if self._deadlock_api_list_endpoints_tool is not None:
            await self._deadlock_api_list_endpoints_tool.close()

        # Close Deadlock API endpoint details tool
        if self._deadlock_api_endpoint_details_tool is not None:
            await self._deadlock_api_endpoint_details_tool.close()

        # Close generic Assets API call tool
        if self._assets_api_call_tool is not None:
            await self._assets_api_call_tool.close()

        # Close helper tools
        if self._helper_tools is not None:
            for tool in self._helper_tools.values():
                close_method = getattr(tool, "close", None)
                if callable(close_method):
                    await close_method()

        # Close ClickHouse toolset
        if self._clickhouse_toolset is not None:
            self._clickhouse_toolset.close()


__all__ = [
    "ToolLoadResult",
    "ToolLoadWarning",
    "ToolRegistry",
]
