# Deadlock AI Assistant - ClickHouse Tools Unit Tests

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from packages.api.models import ChatToolEndEvent, ChatToolStartEvent
from packages.tools.clickhouse import (
    CLICKHOUSE_DBNAME_ENV,
    CLICKHOUSE_HOST_ENV,
    CLICKHOUSE_PASSWORD_ENV,
    CLICKHOUSE_USERNAME_ENV,
    ClickHouseConnectionError,
    ClickHouseDescribeTableTool,
    ClickHouseListTablesTool,
    ClickHouseQueryTool,
    ClickHouseToolset,
    ClickHouseToolsetState,
    TablesNotListedError,
    get_clickhouse_client,
    get_clickhouse_config,
)


class MockQueryResult:
    """Mock ClickHouse query result object."""

    def __init__(
        self,
        result_rows: list[tuple[Any, ...]],
        column_names: list[str] | None = None,
    ) -> None:
        self.result_rows = result_rows
        self.column_names = column_names or []


class MockClient:
    """Mock ClickHouse client object."""

    def __init__(
        self,
        tables: list[str] | None = None,
        describe_result: list[tuple[Any, ...]] | None = None,
        query_result: MockQueryResult | None = None,
    ) -> None:
        self._tables = tables or []
        self._describe_result = describe_result
        self._query_result = query_result
        self._closed = False

    def query(self, sql: str) -> MockQueryResult:  # noqa: ARG002
        if "SHOW TABLES" in sql:
            return MockQueryResult(result_rows=[(t,) for t in self._tables])
        if "DESCRIBE TABLE" in sql:
            return MockQueryResult(result_rows=self._describe_result or [])
        return self._query_result or MockQueryResult(result_rows=[])

    def close(self) -> None:
        self._closed = True


class TestGetClickHouseConfig:
    """Tests for get_clickhouse_config function."""

    def test_get_config_success(self) -> None:
        """Test successful config loading with all env vars."""
        env_vars = {
            CLICKHOUSE_HOST_ENV: "clickhouse.example.com",
            CLICKHOUSE_DBNAME_ENV: "testdb",
            CLICKHOUSE_USERNAME_ENV: "testuser",
            CLICKHOUSE_PASSWORD_ENV: "testpass",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = get_clickhouse_config()

        assert config["host"] == "clickhouse.example.com"
        assert config["database"] == "testdb"
        assert config["username"] == "testuser"
        assert config["password"] == "testpass"

    def test_get_config_missing_host(self) -> None:
        """Test error when CLICKHOUSE_HOST is missing."""
        env_vars = {
            CLICKHOUSE_DBNAME_ENV: "testdb",
            CLICKHOUSE_USERNAME_ENV: "testuser",
            CLICKHOUSE_PASSWORD_ENV: "testpass",
        }
        with (
            patch.dict(os.environ, env_vars, clear=False),
            patch.dict(os.environ, {CLICKHOUSE_HOST_ENV: ""}, clear=False),
        ):
            # Need to ensure the host env var is truly empty
            if CLICKHOUSE_HOST_ENV in os.environ:
                del os.environ[CLICKHOUSE_HOST_ENV]
            with pytest.raises(ClickHouseConnectionError) as exc_info:
                get_clickhouse_config()
            assert CLICKHOUSE_HOST_ENV in str(exc_info.value)

    def test_get_config_missing_multiple_vars(self) -> None:
        """Test error lists all missing variables."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ClickHouseConnectionError) as exc_info:
                get_clickhouse_config()
            error_msg = str(exc_info.value)
            assert CLICKHOUSE_HOST_ENV in error_msg
            assert CLICKHOUSE_DBNAME_ENV in error_msg
            assert CLICKHOUSE_USERNAME_ENV in error_msg
            assert CLICKHOUSE_PASSWORD_ENV in error_msg


class TestGetClickHouseClient:
    """Tests for get_clickhouse_client function."""

    def test_get_client_success(self) -> None:
        """Test successful client creation."""
        env_vars = {
            CLICKHOUSE_HOST_ENV: "clickhouse.example.com",
            CLICKHOUSE_DBNAME_ENV: "testdb",
            CLICKHOUSE_USERNAME_ENV: "testuser",
            CLICKHOUSE_PASSWORD_ENV: "testpass",
        }
        mock_client = MagicMock()

        with (
            patch.dict(os.environ, env_vars, clear=False),
            patch("packages.tools.clickhouse.clickhouse_connect.get_client", return_value=mock_client),
        ):
            client = get_clickhouse_client()

        assert client == mock_client

    def test_get_client_uses_secure_connection(self) -> None:
        """Test that client uses secure (HTTPS) connection."""
        env_vars = {
            CLICKHOUSE_HOST_ENV: "clickhouse.example.com",
            CLICKHOUSE_DBNAME_ENV: "testdb",
            CLICKHOUSE_USERNAME_ENV: "testuser",
            CLICKHOUSE_PASSWORD_ENV: "testpass",
        }
        mock_client = MagicMock()

        with (
            patch.dict(os.environ, env_vars, clear=False),
            patch("packages.tools.clickhouse.clickhouse_connect.get_client", return_value=mock_client) as mock_get,
        ):
            get_clickhouse_client()
            mock_get.assert_called_once_with(
                host="clickhouse.example.com",
                database="testdb",
                username="testuser",
                password="testpass",
                secure=True,
            )

    def test_get_client_connection_failure(self) -> None:
        """Test error handling when connection fails."""
        env_vars = {
            CLICKHOUSE_HOST_ENV: "clickhouse.example.com",
            CLICKHOUSE_DBNAME_ENV: "testdb",
            CLICKHOUSE_USERNAME_ENV: "testuser",
            CLICKHOUSE_PASSWORD_ENV: "testpass",
        }

        with (
            patch.dict(os.environ, env_vars, clear=False),
            patch(
                "packages.tools.clickhouse.clickhouse_connect.get_client",
                side_effect=RuntimeError("Connection refused"),
            ),
        ):
            with pytest.raises(ClickHouseConnectionError) as exc_info:
                get_clickhouse_client()
            assert "Failed to connect" in str(exc_info.value)

    def test_get_client_missing_credentials(self) -> None:
        """Test error when credentials are missing."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ClickHouseConnectionError) as exc_info:
                get_clickhouse_client()
            assert "Missing required environment variables" in str(exc_info.value)


class TestClickHouseToolset:
    """Tests for ClickHouseToolset class."""

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

    def test_toolset_lazy_initialization(self, sse_callback: Any) -> None:
        """Test that client is not created until first use."""
        toolset = ClickHouseToolset(sse_callback=sse_callback)
        assert toolset._client is None

    def test_toolset_get_client_initializes_once(self, sse_callback: Any) -> None:
        """Test that client is initialized only once."""
        mock_client = MockClient()
        toolset = ClickHouseToolset(sse_callback=sse_callback)

        with patch("packages.tools.clickhouse.get_clickhouse_client", return_value=mock_client) as mock_get:
            # First call initializes
            client1 = toolset._get_client()
            assert client1 == mock_client
            assert mock_get.call_count == 1

            # Second call returns cached client
            client2 = toolset._get_client()
            assert client2 == mock_client
            assert mock_get.call_count == 1  # Not called again

    def test_toolset_close(self, sse_callback: Any) -> None:
        """Test that close method closes the client."""
        mock_client = MockClient()
        toolset = ClickHouseToolset(sse_callback=sse_callback)
        toolset._client = mock_client  # type: ignore[assignment]

        toolset.close()
        assert mock_client._closed is True
        assert toolset._client is None

    def test_toolset_close_when_not_initialized(self, sse_callback: Any) -> None:
        """Test that close handles uninitialized client gracefully."""
        toolset = ClickHouseToolset(sse_callback=sse_callback)
        toolset.close()  # Should not raise
        assert toolset._client is None

    def test_toolset_get_tools_returns_all_tools(self, sse_callback: Any) -> None:
        """Test that get_tools returns all three tool types."""
        toolset = ClickHouseToolset(sse_callback=sse_callback)
        tools = toolset.get_tools()

        assert len(tools) == 3
        tool_types = {type(t) for t in tools}
        assert ClickHouseListTablesTool in tool_types
        assert ClickHouseDescribeTableTool in tool_types
        assert ClickHouseQueryTool in tool_types


class TestClickHouseListTablesTool:
    """Tests for ClickHouseListTablesTool."""

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
    def mock_client(self) -> MockClient:
        """Create mock ClickHouse client."""
        return MockClient(tables=["matches", "players", "events"])

    @pytest.fixture
    def state(self) -> ClickHouseToolsetState:
        """Create shared toolset state."""
        return ClickHouseToolsetState()

    @pytest.fixture
    def list_tables_tool(
        self, sse_callback: Any, mock_client: MockClient, state: ClickHouseToolsetState
    ) -> ClickHouseListTablesTool:
        """Create ClickHouseListTablesTool instance."""
        return ClickHouseListTablesTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )

    @pytest.mark.asyncio
    async def test_list_tables_returns_table_names(self, list_tables_tool: ClickHouseListTablesTool) -> None:
        """Test list tables returns list of table names."""
        result = await list_tables_tool._run()
        assert result == ["matches", "players", "events"]

    @pytest.mark.asyncio
    async def test_list_tables_marks_state_as_listed(
        self, list_tables_tool: ClickHouseListTablesTool, state: ClickHouseToolsetState
    ) -> None:
        """Test that list_tables marks the state so other tools can be used."""
        assert state.tables_listed is False
        await list_tables_tool._run()
        assert state.tables_listed is True

    @pytest.mark.asyncio
    async def test_list_tables_empty_database(self, sse_callback: Any, state: ClickHouseToolsetState) -> None:
        """Test list tables returns empty list for empty database."""
        mock_client = MockClient(tables=[])
        tool = ClickHouseListTablesTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )
        result = await tool._run()
        assert result == []

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self,
        list_tables_tool: ClickHouseListTablesTool,
        sse_events: list[ChatToolStartEvent | ChatToolEndEvent],
    ) -> None:
        """Test execute emits start and end SSE events."""
        await list_tables_tool.execute()

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "clickhouse_list_tables"
        assert start_event.arguments == {}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.tool_name == "clickhouse_list_tables"
        assert end_event.success is True
        assert "3 tables" in end_event.result_summary

    def test_name_property(self, list_tables_tool: ClickHouseListTablesTool) -> None:
        """Test name property returns correct name."""
        assert list_tables_tool.name == "clickhouse_list_tables"

    def test_result_summary_with_tables(self, list_tables_tool: ClickHouseListTablesTool) -> None:
        """Test result summary formatting with tables."""
        summary = list_tables_tool._create_result_summary(["table1", "table2"])
        assert summary == "Found 2 tables"

    def test_result_summary_empty(self, list_tables_tool: ClickHouseListTablesTool) -> None:
        """Test result summary formatting with no tables."""
        summary = list_tables_tool._create_result_summary([])
        assert summary == "Found 0 tables"

    def test_get_definition(self, list_tables_tool: ClickHouseListTablesTool) -> None:
        """Test get_definition returns correct tool definition."""
        definition = list_tables_tool.get_definition()
        assert definition["name"] == "clickhouse_list_tables"
        assert "description" in definition
        assert definition["parameters"]["required"] == []


class TestClickHouseDescribeTableTool:
    """Tests for ClickHouseDescribeTableTool."""

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
    def state(self) -> ClickHouseToolsetState:
        """Create shared toolset state with tables already listed."""
        state = ClickHouseToolsetState()
        state.mark_tables_listed()  # Pre-mark as listed for most tests
        return state

    @pytest.fixture
    def unlisted_state(self) -> ClickHouseToolsetState:
        """Create shared toolset state without tables listed."""
        return ClickHouseToolsetState()

    @pytest.fixture
    def mock_client(self) -> MockClient:
        """Create mock ClickHouse client with describe result."""
        return MockClient(
            tables=["matches", "players"],
            describe_result=[
                ("id", "UInt64", "", "", "Primary key"),
                ("name", "String", "", "", "Player name"),
                ("score", "Int32", "", "", ""),
            ],
        )

    @pytest.fixture
    def describe_table_tool(
        self, sse_callback: Any, mock_client: MockClient, state: ClickHouseToolsetState
    ) -> ClickHouseDescribeTableTool:
        """Create ClickHouseDescribeTableTool instance with tables pre-listed."""
        return ClickHouseDescribeTableTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )

    @pytest.mark.asyncio
    async def test_describe_table_requires_list_tables_first(
        self, sse_callback: Any, mock_client: MockClient, unlisted_state: ClickHouseToolsetState
    ) -> None:
        """Test describe table raises TablesNotListedError if list_tables not called first."""
        tool = ClickHouseDescribeTableTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=unlisted_state,
        )
        with pytest.raises(TablesNotListedError) as exc_info:
            await tool._run(table_name="matches")
        assert "clickhouse_list_tables first" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_describe_table_returns_columns(self, describe_table_tool: ClickHouseDescribeTableTool) -> None:
        """Test describe table returns column information."""
        result = await describe_table_tool._run(table_name="matches")

        assert len(result) == 3
        assert result[0]["name"] == "id"
        assert result[0]["type"] == "UInt64"
        assert result[0]["comment"] == "Primary key"
        assert result[1]["name"] == "name"
        assert result[1]["type"] == "String"
        assert "comment" not in result[2]  # Empty comment is not included

    @pytest.mark.asyncio
    async def test_describe_table_empty_table_name(self, describe_table_tool: ClickHouseDescribeTableTool) -> None:
        """Test describe table raises ValueError for empty table name."""
        with pytest.raises(ValueError) as exc_info:
            await describe_table_tool._run(table_name="")
        assert "Invalid table name" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_describe_table_invalid_characters(self, describe_table_tool: ClickHouseDescribeTableTool) -> None:
        """Test describe table raises ValueError for invalid characters."""
        with pytest.raises(ValueError) as exc_info:
            await describe_table_tool._run(table_name="table; DROP TABLE users;")
        assert "Invalid table name" in str(exc_info.value)
        assert "alphanumeric" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_describe_table_with_special_chars(self, describe_table_tool: ClickHouseDescribeTableTool) -> None:
        """Test describe table rejects table names with special chars."""
        invalid_names = ["table-name", "table.name", "table name", "table@name"]
        for name in invalid_names:
            with pytest.raises(ValueError) as exc_info:
                await describe_table_tool._run(table_name=name)
            assert "Invalid table name" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_describe_table_not_found(self, sse_callback: Any, state: ClickHouseToolsetState) -> None:
        """Test describe table raises error for missing table."""
        mock_client = MockClient(tables=["other_table"])
        tool = ClickHouseDescribeTableTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )
        with pytest.raises(ValueError) as exc_info:
            await tool._run(table_name="nonexistent")
        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_describe_table_valid_name_patterns(self, describe_table_tool: ClickHouseDescribeTableTool) -> None:
        """Test describe table accepts valid name patterns."""
        valid_names = ["table_name", "TableName", "table123", "_table", "TABLE_NAME_123"]
        for name in valid_names:
            # Should not raise ValueError for valid patterns (may raise for table not found)
            try:
                await describe_table_tool._run(table_name=name)
            except ValueError as e:
                # Only acceptable if it's "not found" error, not "invalid" error
                assert "not found" in str(e), f"Unexpected error for {name}: {e}"

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self,
        describe_table_tool: ClickHouseDescribeTableTool,
        sse_events: list[ChatToolStartEvent | ChatToolEndEvent],
    ) -> None:
        """Test execute emits start and end SSE events."""
        await describe_table_tool.execute(table_name="matches")

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "clickhouse_describe_table"
        assert start_event.arguments == {"table_name": "matches"}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.tool_name == "clickhouse_describe_table"
        assert end_event.success is True
        assert "3 columns" in end_event.result_summary

    @pytest.mark.asyncio
    async def test_execute_emits_error_event_on_failure(
        self,
        describe_table_tool: ClickHouseDescribeTableTool,
        sse_events: list[ChatToolStartEvent | ChatToolEndEvent],
    ) -> None:
        """Test execute emits error end event on failure."""
        with pytest.raises(ValueError):
            await describe_table_tool.execute(table_name="")

        assert len(sse_events) == 2
        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is False
        assert "Error:" in end_event.result_summary

    def test_name_property(self, describe_table_tool: ClickHouseDescribeTableTool) -> None:
        """Test name property returns correct name."""
        assert describe_table_tool.name == "clickhouse_describe_table"

    def test_result_summary_with_columns(self, describe_table_tool: ClickHouseDescribeTableTool) -> None:
        """Test result summary formatting with columns."""
        columns = [{"name": "col1", "type": "String"}, {"name": "col2", "type": "Int32"}]
        summary = describe_table_tool._create_result_summary(columns)
        assert summary == "Described 2 columns"

    def test_get_definition(self, describe_table_tool: ClickHouseDescribeTableTool) -> None:
        """Test get_definition returns correct tool definition."""
        definition = describe_table_tool.get_definition()
        assert definition["name"] == "clickhouse_describe_table"
        assert "description" in definition
        assert "table_name" in definition["parameters"]["properties"]
        assert "table_name" in definition["parameters"]["required"]


class TestClickHouseQueryTool:
    """Tests for ClickHouseQueryTool."""

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
    def state(self) -> ClickHouseToolsetState:
        """Create shared toolset state with tables already listed."""
        state = ClickHouseToolsetState()
        state.mark_tables_listed()  # Pre-mark as listed for most tests
        return state

    @pytest.fixture
    def unlisted_state(self) -> ClickHouseToolsetState:
        """Create shared toolset state without tables listed."""
        return ClickHouseToolsetState()

    @pytest.fixture
    def mock_client(self) -> MockClient:
        """Create mock ClickHouse client with query result."""
        result = MockQueryResult(
            result_rows=[(1, "player1", 100), (2, "player2", 200)],
            column_names=["id", "name", "score"],
        )
        return MockClient(query_result=result)

    @pytest.fixture
    def query_tool(
        self, sse_callback: Any, mock_client: MockClient, state: ClickHouseToolsetState
    ) -> ClickHouseQueryTool:
        """Create ClickHouseQueryTool instance with tables pre-listed."""
        return ClickHouseQueryTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )

    @pytest.mark.asyncio
    async def test_query_requires_list_tables_first(
        self, sse_callback: Any, mock_client: MockClient, unlisted_state: ClickHouseToolsetState
    ) -> None:
        """Test query raises TablesNotListedError if list_tables not called first."""
        tool = ClickHouseQueryTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=unlisted_state,
        )
        with pytest.raises(TablesNotListedError) as exc_info:
            await tool._run(sql="SELECT * FROM players")
        assert "clickhouse_list_tables first" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_returns_results(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query returns list of dictionaries."""
        result = await query_tool._run(sql="SELECT * FROM players")

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["name"] == "player1"
        assert result[0]["score"] == 100
        assert result[1]["id"] == 2

    @pytest.mark.asyncio
    async def test_query_empty_result(self, sse_callback: Any, state: ClickHouseToolsetState) -> None:
        """Test query with no results."""
        mock_client = MockClient(query_result=MockQueryResult(result_rows=[], column_names=["id"]))
        tool = ClickHouseQueryTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )
        result = await tool._run(sql="SELECT * FROM empty_table")
        assert result == []

    @pytest.mark.asyncio
    async def test_query_empty_sql_raises_error(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query raises ValueError for empty SQL."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="")
        assert "cannot be empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_whitespace_sql_raises_error(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query raises ValueError for whitespace-only SQL."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="   ")
        assert "cannot be empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_rejects_insert(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query rejects INSERT statements."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="INSERT INTO players VALUES (1, 'test', 100)")
        assert "Only SELECT queries are allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_rejects_update(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query rejects UPDATE statements."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="UPDATE players SET score = 100")
        assert "Only SELECT queries are allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_rejects_delete(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query rejects DELETE statements."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="DELETE FROM players WHERE id = 1")
        assert "Only SELECT queries are allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_rejects_drop(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query rejects DROP statements."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="DROP TABLE players")
        assert "Only SELECT queries are allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_rejects_create(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query rejects CREATE statements."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="CREATE TABLE test (id UInt64)")
        assert "Only SELECT queries are allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_rejects_truncate(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query rejects TRUNCATE statements."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="TRUNCATE TABLE players")
        assert "Only SELECT queries are allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_rejects_alter(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query rejects ALTER statements."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="ALTER TABLE players ADD COLUMN age UInt8")
        assert "Only SELECT queries are allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_requires_select_start(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query requires SELECT or WITH at start."""
        with pytest.raises(ValueError) as exc_info:
            await query_tool._run(sql="SHOW TABLES")  # Not SELECT or WITH
        assert "must start with SELECT or WITH" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_allows_select(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query allows SELECT statements."""
        result = await query_tool._run(sql="SELECT * FROM players")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_query_allows_with_cte(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query allows WITH (CTE) statements."""
        result = await query_tool._run(sql="WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_query_case_insensitive_validation(self, query_tool: ClickHouseQueryTool) -> None:
        """Test query validation is case insensitive."""
        # Should reject regardless of case
        disallowed = ["insert", "INSERT", "Insert", "delete", "DELETE", "drop", "DROP"]
        for keyword in disallowed:
            with pytest.raises(ValueError):
                await query_tool._run(sql=f"{keyword} INTO/FROM players")

    @pytest.mark.asyncio
    async def test_query_respects_row_limit(self, sse_callback: Any, state: ClickHouseToolsetState) -> None:
        """Test query respects custom row limit."""
        rows = [(i,) for i in range(100)]
        mock_client = MockClient(query_result=MockQueryResult(result_rows=rows, column_names=["id"]))
        tool = ClickHouseQueryTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
            row_limit=10,
        )
        result = await tool._run(sql="SELECT * FROM large_table")
        assert len(result) <= 10

    @pytest.mark.asyncio
    async def test_query_timeout_error(self, sse_callback: Any, state: ClickHouseToolsetState) -> None:
        """Test query handles timeout errors."""
        mock_client = MagicMock()
        mock_client.query.side_effect = RuntimeError("Query timed out")

        tool = ClickHouseQueryTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )
        with pytest.raises(TimeoutError) as exc_info:
            await tool._run(sql="SELECT * FROM players")
        assert "timed out" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self,
        query_tool: ClickHouseQueryTool,
        sse_events: list[ChatToolStartEvent | ChatToolEndEvent],
    ) -> None:
        """Test execute emits start and end SSE events."""
        await query_tool.execute(sql="SELECT * FROM players")

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "clickhouse_query"
        assert "sql" in start_event.arguments

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.tool_name == "clickhouse_query"
        assert end_event.success is True
        assert "2 rows" in end_event.result_summary

    @pytest.mark.asyncio
    async def test_execute_emits_error_event_on_failure(
        self,
        query_tool: ClickHouseQueryTool,
        sse_events: list[ChatToolStartEvent | ChatToolEndEvent],
    ) -> None:
        """Test execute emits error end event on failure."""
        with pytest.raises(ValueError):
            await query_tool.execute(sql="")

        assert len(sse_events) == 2
        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is False
        assert "Error:" in end_event.result_summary

    def test_name_property(self, query_tool: ClickHouseQueryTool) -> None:
        """Test name property returns correct name."""
        assert query_tool.name == "clickhouse_query"

    def test_result_summary_with_rows(self, query_tool: ClickHouseQueryTool) -> None:
        """Test result summary formatting with rows."""
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        summary = query_tool._create_result_summary(rows)
        assert summary == "Returned 3 rows"

    def test_result_summary_empty_rows(self, query_tool: ClickHouseQueryTool) -> None:
        """Test result summary formatting with no rows."""
        summary = query_tool._create_result_summary([])
        assert summary == "Returned 0 rows"

    def test_truncate_query_short(self, query_tool: ClickHouseQueryTool) -> None:
        """Test truncate query for short query."""
        sql = "SELECT * FROM players"
        truncated = query_tool._truncate_query(sql)
        assert truncated == sql

    def test_truncate_query_long(self, query_tool: ClickHouseQueryTool) -> None:
        """Test truncate query for long query."""
        sql = "SELECT " + "a, " * 100 + "b FROM very_long_query_table"
        truncated = query_tool._truncate_query(sql)
        assert len(truncated) <= query_tool.MAX_QUERY_SUMMARY_LENGTH
        assert truncated.endswith("...")

    def test_get_definition(self, query_tool: ClickHouseQueryTool) -> None:
        """Test get_definition returns correct tool definition."""
        definition = query_tool.get_definition()
        assert definition["name"] == "clickhouse_query"
        assert "description" in definition
        assert "sql" in definition["parameters"]["properties"]
        assert "sql" in definition["parameters"]["required"]
        # Check that description guides agent to prefer simple queries
        assert "simple" in definition["description"].lower()


class TestClickHouseToolsIntegration:
    """Integration tests for ClickHouse tools."""

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
    def state(self) -> ClickHouseToolsetState:
        """Create shared toolset state."""
        return ClickHouseToolsetState()

    @pytest.mark.asyncio
    async def test_list_then_describe_workflow(self, sse_callback: Any, state: ClickHouseToolsetState) -> None:
        """Test typical workflow: list tables then describe one."""
        mock_client = MockClient(
            tables=["matches", "players", "events"],
            describe_result=[
                ("match_id", "UInt64", "", "", "Match identifier"),
                ("player_id", "UInt64", "", "", "Player identifier"),
            ],
        )

        list_tool = ClickHouseListTablesTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )
        describe_tool = ClickHouseDescribeTableTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )

        # List tables first (required before describe)
        tables = await list_tool.execute()
        assert "matches" in tables

        # Now describe is allowed since we listed tables
        columns = await describe_tool.execute(table_name="matches")
        assert len(columns) == 2
        assert columns[0]["name"] == "match_id"

    @pytest.mark.asyncio
    async def test_describe_without_list_fails(self, sse_callback: Any, state: ClickHouseToolsetState) -> None:
        """Test that describe fails if list_tables not called first."""
        mock_client = MockClient(
            tables=["matches"],
            describe_result=[("id", "UInt64", "", "", "")],
        )

        describe_tool = ClickHouseDescribeTableTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )

        # Should fail because we haven't listed tables yet
        with pytest.raises(TablesNotListedError):
            await describe_tool.execute(table_name="matches")

    @pytest.mark.asyncio
    async def test_list_describe_query_workflow(
        self, sse_callback: Any, sse_events: list[ChatToolStartEvent | ChatToolEndEvent], state: ClickHouseToolsetState
    ) -> None:
        """Test workflow: list tables, describe table, then run query."""
        # Create a special mock client that handles SHOW TABLES, DESCRIBE and SELECT queries
        mock_describe_result = MockQueryResult(
            result_rows=[
                ("id", "UInt64", "", "", ""),
                ("name", "String", "", "", ""),
            ],
        )
        mock_query_result = MockQueryResult(
            result_rows=[(1, "test")],
            column_names=["id", "name"],
        )

        class SmartMockClient:
            def __init__(self) -> None:
                self._tables = ["players"]

            def query(self, sql: str) -> MockQueryResult:
                if "SHOW TABLES" in sql:
                    return MockQueryResult(result_rows=[("players",)])
                if "DESCRIBE TABLE" in sql:
                    return mock_describe_result
                return mock_query_result

        mock_client = SmartMockClient()

        list_tool = ClickHouseListTablesTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )
        describe_tool = ClickHouseDescribeTableTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )
        query_tool = ClickHouseQueryTool(
            sse_callback=sse_callback,
            timeout=10.0,
            get_client=lambda: mock_client,
            state=state,
        )

        # First list tables (required)
        tables = await list_tool.execute()
        assert "players" in tables

        # Describe table to understand schema
        columns = await describe_tool.execute(table_name="players")
        column_names = [c["name"] for c in columns]
        assert "id" in column_names
        assert "name" in column_names

        # Run query based on schema
        result = await query_tool.execute(sql="SELECT id, name FROM players")
        assert len(result) == 1
        assert result[0]["id"] == 1

        # Verify SSE events were emitted for all operations
        assert len(sse_events) == 6  # 2 for list, 2 for describe, 2 for query

    @pytest.mark.asyncio
    async def test_toolset_provides_working_tools(self, sse_callback: Any) -> None:
        """Test that toolset provides tools that can execute."""
        mock_client = MockClient(tables=["test_table"])

        with patch("packages.tools.clickhouse.get_clickhouse_client", return_value=mock_client):
            toolset = ClickHouseToolset(sse_callback=sse_callback)
            tools = toolset.get_tools()

            # Find list tables tool
            list_tool = next(t for t in tools if isinstance(t, ClickHouseListTablesTool))

            # Execute it
            result = await list_tool.execute()
            assert result == ["test_table"]

            toolset.close()
