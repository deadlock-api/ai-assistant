# Deadlock AI Assistant - ClickHouse tools package
# Tools for interacting with ClickHouse database

import os
import re
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver import Client

from packages.tools.base import BaseTool, SSECallback, retry


class ClickHouseConnectionError(Exception):
    """Raised when unable to connect to ClickHouse or credentials are missing."""


# Environment variable names for ClickHouse configuration
CLICKHOUSE_HOST_ENV = "CLICKHOUSE_HOST"
CLICKHOUSE_DBNAME_ENV = "CLICKHOUSE_DBNAME"
CLICKHOUSE_USERNAME_ENV = "CLICKHOUSE_USERNAME"
CLICKHOUSE_PASSWORD_ENV = "CLICKHOUSE_PASSWORD"


def get_clickhouse_config() -> dict[str, str]:
    """Load ClickHouse connection configuration from environment variables.

    Returns:
        Dictionary with host, database, username, and password

    Raises:
        ClickHouseConnectionError: If any required environment variable is missing
    """
    required_vars = [
        CLICKHOUSE_HOST_ENV,
        CLICKHOUSE_DBNAME_ENV,
        CLICKHOUSE_USERNAME_ENV,
        CLICKHOUSE_PASSWORD_ENV,
    ]

    missing = [var for var in required_vars if not os.environ.get(var)]

    if missing:
        raise ClickHouseConnectionError(
            f"Missing required environment variables for ClickHouse connection: {', '.join(missing)}"
        )

    # After validation, all variables are guaranteed to exist
    return {
        "host": os.environ[CLICKHOUSE_HOST_ENV],
        "database": os.environ[CLICKHOUSE_DBNAME_ENV],
        "username": os.environ[CLICKHOUSE_USERNAME_ENV],
        "password": os.environ[CLICKHOUSE_PASSWORD_ENV],
    }


def get_clickhouse_client() -> Client:
    """Create a ClickHouse client connection.

    Returns:
        Configured ClickHouse client

    Raises:
        ClickHouseConnectionError: If credentials are missing or connection fails
    """
    config = get_clickhouse_config()

    try:
        client: Client = clickhouse_connect.get_client(
            host=config["host"],
            database=config["database"],
            username=config["username"],
            password=config["password"],
            secure=False,  # Use HTTP
        )
        return client
    except Exception as e:
        raise ClickHouseConnectionError(f"Failed to connect to ClickHouse: {e}") from e


class ClickHouseToolsetState:
    """Shared state for ClickHouse tools within a toolset.

    Tracks whether the agent has listed tables before attempting to describe or query them.
    This ensures the agent understands the available schema before running queries.
    """

    def __init__(self) -> None:
        self._tables_listed: bool = False

    @property
    def tables_listed(self) -> bool:
        """Check if tables have been listed in this session."""
        return self._tables_listed

    def mark_tables_listed(self) -> None:
        """Mark that tables have been listed."""
        self._tables_listed = True

    def reset(self) -> None:
        """Reset the state (e.g., for testing or new sessions)."""
        self._tables_listed = False


class ClickHouseToolset:
    """Manages ClickHouse tools with lazy client initialization.

    Provides shared ClickHouse client connection for all tools in this toolset.
    The client is lazily initialized on first use.

    Enforces that clickhouse_list_tables must be called before clickhouse_describe_table
    or clickhouse_query can be used. This ensures the agent understands the available
    schema before attempting to query the database.

    Args:
        sse_callback: Callback function to emit SSE events
        timeout: Default timeout for tool executions (default: 60.0)
    """

    def __init__(self, sse_callback: SSECallback, timeout: float = 60.0) -> None:
        self._sse_callback = sse_callback
        self._timeout = timeout
        self._client: Client | None = None
        self._state = ClickHouseToolsetState()

    def _get_client(self) -> Client:
        """Lazy initialization of ClickHouse client connection."""
        if self._client is None:
            self._client = get_clickhouse_client()
        return self._client

    def close(self) -> None:
        """Close the ClickHouse client connection if open."""
        if self._client is not None:
            self._client.close()
            self._client = None
        self._state.reset()

    def get_tools(
        self,
    ) -> list[ClickHouseListTablesTool | ClickHouseDescribeTableTool | ClickHouseQueryTool]:
        """Get all ClickHouse tools.

        Returns:
            List of ClickHouse tool instances
        """
        return [
            ClickHouseListTablesTool(self._sse_callback, self._timeout, self._get_client, self._state),
            ClickHouseDescribeTableTool(self._sse_callback, self._timeout, self._get_client, self._state),
            ClickHouseQueryTool(self._sse_callback, self._timeout, self._get_client, self._state),
        ]


class TablesNotListedError(Exception):
    """Raised when trying to describe or query before listing tables."""


class ClickHouseDescribeTableTool(BaseTool):
    """Tool to describe a table's schema in the ClickHouse database.

    Returns column names, types, and comments for the specified table.
    Requires clickhouse_list_tables to be called first.
    """

    # Pattern for valid table names: alphanumeric and underscore only
    TABLE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float,
        get_client: Any,  # Callable[[], Client] - use Any to avoid import issues
        state: ClickHouseToolsetState,
    ) -> None:
        super().__init__(sse_callback, timeout)
        self._get_client = get_client
        self._state = state

    @property
    def name(self) -> str:
        return "clickhouse_describe_table"

    @retry(max_attempts=3)
    async def _run(self, table_name: str) -> list[dict[str, str]]:
        """Describe a table's schema in the ClickHouse database.

        Args:
            table_name: Name of the table to describe

        Returns:
            List of column information dictionaries with name, type, and comment

        Raises:
            TablesNotListedError: If clickhouse_list_tables was not called first
            ValueError: If table_name contains invalid characters
        """
        # Check if tables have been listed first
        if not self._state.tables_listed:
            raise TablesNotListedError(
                "You must call clickhouse_list_tables first before describing a table. "
                "This ensures you understand the available schema before querying."
            )

        # Validate table name
        if not table_name or not self.TABLE_NAME_PATTERN.match(table_name):
            raise ValueError(
                f"Invalid table name '{table_name}'. "
                "Table names must contain only alphanumeric characters and underscores."
            )

        client = self._get_client()

        # Check if table exists first
        tables_result = client.query("SHOW TABLES")
        table_names = [row[0] for row in tables_result.result_rows]
        if table_name not in table_names:
            raise ValueError(f"Table '{table_name}' not found in database")

        # Get table description
        result = client.query(f"DESCRIBE TABLE {table_name}")

        # Result columns: name, type, default_type, default_expression, comment, ...
        columns: list[dict[str, str]] = []
        for row in result.result_rows:
            column_info: dict[str, str] = {
                "name": str(row[0]),
                "type": str(row[1]),
            }
            # Include comment if present (column index 4)
            if len(row) > 4 and row[4]:
                column_info["comment"] = str(row[4])
            columns.append(column_info)

        return columns

    def _create_result_summary(self, result: list[dict[str, str]]) -> str:
        """Create a summary showing the column count."""
        return f"Described {len(result)} columns"

    def get_definition(self) -> dict[str, Any]:
        """Get the tool definition for agent configuration.

        Returns:
            Tool definition dictionary
        """
        return {
            "name": self.name,
            "description": "Describe a table's schema in the ClickHouse database. "
            "Returns column names, types, and comments. "
            "PREREQUISITE: You MUST call clickhouse_list_tables first before using this tool. "
            "Use this to understand table structure before writing queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Name of the table to describe (alphanumeric and underscores only)",
                    }
                },
                "required": ["table_name"],
            },
        }


class ClickHouseQueryTool(BaseTool):
    """Tool to run SELECT queries against the ClickHouse database.

    Executes read-only SQL queries and returns results as list of dictionaries.
    Only SELECT queries are allowed; data modification queries are rejected.
    Requires clickhouse_list_tables to be called first.
    """

    # Default maximum number of result rows
    DEFAULT_ROW_LIMIT = 1000

    # Maximum length for query in result summary
    MAX_QUERY_SUMMARY_LENGTH = 100

    # Pattern to detect disallowed SQL statements (case-insensitive)
    # Matches statements that start with these keywords
    DISALLOWED_SQL_PATTERN = re.compile(
        r"^\s*(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|RENAME|GRANT|REVOKE|ATTACH|DETACH|KILL)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float,
        get_client: Any,  # Callable[[], Client] - use Any to avoid import issues
        state: ClickHouseToolsetState,
        row_limit: int = DEFAULT_ROW_LIMIT,
    ) -> None:
        super().__init__(sse_callback, timeout)
        self._get_client = get_client
        self._state = state
        self._row_limit = row_limit

    @property
    def name(self) -> str:
        return "clickhouse_query"

    @retry(max_attempts=3)
    async def _run(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SELECT query against the ClickHouse database.

        Args:
            sql: SQL query to execute (must be a SELECT query)

        Returns:
            List of row dictionaries with column names as keys

        Raises:
            TablesNotListedError: If clickhouse_list_tables was not called first
            ValueError: If sql is empty or contains disallowed statements
            TimeoutError: If query execution times out
        """
        # Check if tables have been listed first
        if not self._state.tables_listed:
            raise TablesNotListedError(
                "You must call clickhouse_list_tables first before running queries. "
                "This ensures you understand the available schema before querying."
            )

        # Validate input is non-empty
        if not sql or not sql.strip():
            raise ValueError("SQL query cannot be empty")

        sql = sql.strip()

        # Validate query is SELECT-only
        if self.DISALLOWED_SQL_PATTERN.match(sql):
            raise ValueError(
                "Only SELECT queries are allowed. "
                "INSERT, UPDATE, DELETE, DROP, and other data modification queries are not permitted."
            )

        # Also ensure query starts with SELECT (or WITH for CTEs)
        sql_upper = sql.upper()
        if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
            raise ValueError("Query must start with SELECT or WITH (for CTEs). Only read operations are permitted.")

        client = self._get_client()

        try:
            # Add LIMIT if not present to prevent excessive data retrieval
            if "LIMIT" not in sql_upper:
                sql = f"{sql} LIMIT {self._row_limit}"

            # Execute query with timeout from BaseTool
            result = client.query(sql)

            # Convert result to list of dictionaries
            column_names = result.column_names
            rows: list[dict[str, Any]] = []
            for row in result.result_rows[: self._row_limit]:  # Enforce row limit
                row_dict: dict[str, Any] = {}
                for i, col_name in enumerate(column_names):
                    row_dict[col_name] = row[i]
                rows.append(row_dict)

            return rows

        except Exception as e:
            # Check if it's a timeout error
            error_msg = str(e).lower()
            if "timeout" in error_msg or "timed out" in error_msg:
                raise TimeoutError(f"Query execution timed out: {e}") from e
            raise

    def _create_result_summary(self, result: list[dict[str, Any]]) -> str:
        """Create a summary showing the row count."""
        return f"Returned {len(result)} rows"

    def _truncate_query(self, sql: str) -> str:
        """Truncate query for display in SSE events."""
        if len(sql) <= self.MAX_QUERY_SUMMARY_LENGTH:
            return sql
        return sql[: self.MAX_QUERY_SUMMARY_LENGTH - 3] + "..."

    def get_definition(self) -> dict[str, Any]:
        """Get the tool definition for agent configuration.

        Returns:
            Tool definition dictionary
        """
        return {
            "name": self.name,
            "description": (
                "Execute a SELECT query against the ClickHouse database for analytics. "
                "Only read-only queries are allowed; data modifications are rejected. "
                "Results are limited to 1000 rows by default. "
                "PREREQUISITE: You MUST call clickhouse_list_tables first before using this tool. "
                "IMPORTANT: Prefer running multiple simple, focused queries over complex ones. "
                "For example, run separate queries for different aggregations rather than combining them. "
                "Use clickhouse_describe_table to understand table schema before querying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL SELECT query to execute. Must start with SELECT or WITH (for CTEs).",
                    }
                },
                "required": ["sql"],
            },
        }


class ClickHouseListTablesTool(BaseTool):
    """Tool to list all tables in the ClickHouse database.

    Returns a list of table names in the configured database.
    This tool MUST be called before using clickhouse_describe_table or clickhouse_query.
    """

    def __init__(
        self,
        sse_callback: SSECallback,
        timeout: float,
        get_client: Any,  # Callable[[], Client] - use Any to avoid import issues
        state: ClickHouseToolsetState,
    ) -> None:
        super().__init__(sse_callback, timeout)
        self._get_client = get_client
        self._state = state

    @property
    def name(self) -> str:
        return "clickhouse_list_tables"

    @retry(max_attempts=3)
    async def _run(self) -> list[str]:
        """List all tables in the configured ClickHouse database.

        Returns:
            List of table names
        """
        client = self._get_client()
        result = client.query("SHOW TABLES")
        # Result is a QueryResult; result.result_rows contains tuples of values
        tables: list[str] = [row[0] for row in result.result_rows]
        # Mark that tables have been listed - enables describe_table and query tools
        self._state.mark_tables_listed()
        return tables

    def _create_result_summary(self, result: list[str]) -> str:
        """Create a summary showing the table count."""
        return f"Found {len(result)} tables"

    def get_definition(self) -> dict[str, Any]:
        """Get the tool definition for agent configuration.

        Returns:
            Tool definition dictionary
        """
        return {
            "name": self.name,
            "description": "List all tables in the ClickHouse database. "
            "IMPORTANT: You MUST call this tool first before using clickhouse_describe_table or clickhouse_query. "
            "This ensures you understand the available schema before writing queries.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }


__all__ = [
    "ClickHouseConnectionError",
    "ClickHouseDescribeTableTool",
    "ClickHouseListTablesTool",
    "ClickHouseQueryTool",
    "ClickHouseToolset",
    "ClickHouseToolsetState",
    "TablesNotListedError",
    "get_clickhouse_client",
    "get_clickhouse_config",
    "CLICKHOUSE_HOST_ENV",
    "CLICKHOUSE_DBNAME_ENV",
    "CLICKHOUSE_USERNAME_ENV",
    "CLICKHOUSE_PASSWORD_ENV",
]
