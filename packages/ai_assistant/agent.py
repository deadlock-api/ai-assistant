"""Claude Agent SDK wrapper for Deadlock AI Assistant.

Provides a configured Claude agent using ClaudeSDKClient for:
- Continuous conversation sessions with context preservation
- Streaming support with timeout handling
- Retry policies for transient errors
- Interrupt support for long-running operations
- Custom tool integration via MCP servers

Best Practices Applied (from Claude Agent SDK docs):
- Uses ClaudeSDKClient instead of query() for multi-turn conversations
- Leverages session continuity for context-aware responses
- Supports async context manager for proper resource cleanup

## Custom Tools Implementation

The Claude Agent SDK supports custom tools via the `@tool` decorator and MCP servers.
Tools allow Claude to perform actions like calculations, API calls, or data lookups.

### Basic Tool Definition

```python
from claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeSDKClient
from claude_agent_sdk.types import ClaudeAgentOptions
from typing import Any

# Define a tool using the @tool decorator
@tool("calculate", "Perform mathematical calculations", {"expression": str})
async def calculate(args: dict[str, Any]) -> dict[str, Any]:
    '''Execute a math expression and return the result.'''
    try:
        # SECURITY: Use ast.literal_eval or a math parser in production
        result = eval(args["expression"], {"__builtins__": {}})
        return {
            "content": [{"type": "text", "text": f"Result: {result}"}]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "is_error": True
        }

@tool("get_weather", "Get current weather for a city", {"city": str})
async def get_weather(args: dict[str, Any]) -> dict[str, Any]:
    '''Fetch weather data for the specified city.'''
    city = args["city"]
    # In production, call a real weather API
    return {
        "content": [{"type": "text", "text": f"Weather in {city}: Sunny, 22°C"}]
    }
```

### Input Schema Options

Tools support two schema formats:

1. **Simple type mapping** (recommended for most cases):
   ```python
   @tool("greet", "Greet a user", {"name": str, "formal": bool})
   ```

2. **JSON Schema format** (for complex validation):
   ```python
   @tool("search", "Search with filters", {
       "type": "object",
       "properties": {
           "query": {"type": "string", "minLength": 1},
           "limit": {"type": "integer", "minimum": 1, "maximum": 100}
       },
       "required": ["query"]
   })
   ```

### Creating an MCP Server with Tools

```python
# Bundle tools into an MCP server
my_tools_server = create_sdk_mcp_server(
    name="my_tools",
    version="1.0.0",
    tools=[calculate, get_weather]
)

# Configure the client to use these tools
options = ClaudeAgentOptions(
    mcp_servers={"tools": my_tools_server},
    allowed_tools=[
        "mcp__tools__calculate",
        "mcp__tools__get_weather"
    ]
)

# Use with ClaudeSDKClient
async with ClaudeSDKClient(options=options) as client:
    await client.query("What is 15 * 23?")
    async for msg in client.receive_response():
        print(msg)  # Claude will use the calculate tool
```

### Tool Return Format

Tools must return a dict with a `content` list:

```python
# Success response
return {
    "content": [
        {"type": "text", "text": "Operation successful: ..."}
    ]
}

# Error response
return {
    "content": [
        {"type": "text", "text": "Error: Invalid input"}
    ],
    "is_error": True  # Tells Claude the tool failed
}

# Multiple content blocks
return {
    "content": [
        {"type": "text", "text": "Results:"},
        {"type": "text", "text": "Item 1: ..."},
        {"type": "text", "text": "Item 2: ..."}
    ]
}
```

### Integration with DeadlockAgentClient

To use custom tools with DeadlockAgentClient:

```python
from packages.ai_assistant.agent import AgentConfig

# Create a subclass or factory that adds tool support
def create_client_with_tools(tools_server, allowed_tools: list[str]) -> ClaudeSDKClient:
    options = ClaudeAgentOptions(
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        mcp_servers={"custom": tools_server},
        allowed_tools=allowed_tools,
        max_turns=5,  # Allow multiple turns for tool use
    )
    return ClaudeSDKClient(options=options)
```

### Advanced: Permission Handlers for Tools

Control which tools can be used with a permission callback:

```python
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

async def tool_permission_handler(
    tool_name: str,
    input_data: dict,
    context: dict
) -> PermissionResultAllow | PermissionResultDeny:
    '''Custom logic to approve or deny tool usage.'''

    # Block dangerous operations
    if tool_name == "Bash" and "rm -rf" in str(input_data):
        return PermissionResultDeny(
            message="Dangerous command blocked",
            interrupt=True
        )

    # Modify inputs (e.g., sandbox file paths)
    if tool_name == "Write":
        safe_path = f"./sandbox/{input_data.get('file_path', '')}"
        return PermissionResultAllow(
            updated_input={**input_data, "file_path": safe_path}
        )

    # Allow by default
    return PermissionResultAllow(updated_input=input_data)

options = ClaudeAgentOptions(
    can_use_tool=tool_permission_handler,
    allowed_tools=["mcp__tools__wiki_search"]
)
```
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server, tool
from claude_agent_sdk._errors import ClaudeSDKError, CLIConnectionError, CLINotFoundError, ProcessError
from claude_agent_sdk.types import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock
from toon_format import encode as toon_encode

from packages.api.models import ChatToolEndEvent, ChatToolStartEvent, ChatUsageEvent

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from claude_agent_sdk.types import Message

    from packages.tools.registry import ToolRegistry

# Default system prompt for the Deadlock Assistant
DEFAULT_SYSTEM_PROMPT = """
<system_role>
You are the **Deadlock AI Assistant**, a fusion of **Senior Game Analyst** and **Data Scientist**.
You are precise, insightful, and data-driven. Do not merely answer; provide strategic context,
verify facts via tools, and execute complex analytics.
</system_role>

<core_objectives>
1. **Educate**: Explain mechanics (e.g., Soul Economy, Flex Slots) clearly, referencing Wiki data.
2. **Analyze**: Use SQL/API tools for insights on hero win rates, item pick rates, and match trends.
3. **Verify**: Never hallucinate. If unsure, use tools to confirm.
</core_objectives>

<operational_constraints>
1. **Ambiguity**: Do not guess context. Ask clarifying questions (e.g., "High MMR lobbies?" or "Which lane?").
2. **Citation**: Always cite the source tool (e.g., "[Source: Wiki]" or "[Source: MatchDB]").
3. **SQL Safety**: When using `clickhouse_query`, ALWAYS include a `LIMIT` clause (max 100 rows) unless asking for
   aggregations. Never execute `DROP`, `ALTER`, or `DELETE`.
4. **Honesty**: If tools fail and knowledge is insufficient, state: "I do not have access to that data."
5. **Efficient Lookups**: When you need hero or item ID/name mappings, use `get_hero_mapping` or `get_item_mapping`
   to fetch ALL mappings in one call.
</operational_constraints>

<knowledge_base>
**1. Map & Objectives**
* **Structure**: 3 Lanes. Guardians -> Walkers -> Shrines -> Patron.
* **Traversal**: Sky Rails. "Zip Boost" consumes stamina; taking damage causes stun/dismount.
* **Mid Boss**: Drops "Rejuvenator" (Respawn cut, Minion HP buff, Fire rate buff).
* **Spirit Urn**: Disables casting/shooting while carrying. Deliver for Team Souls + Ability Point.

**2. Economy (Souls)**
* **Secure/Deny**: Shooting orbs secures souls. Shooting enemy orbs "Denies" them.
* **Jungle**: Sinner's Sacrifice (Slot machines) spawn at 10:00; convert Health to Souls.

**3. Hero & Meta**
* **Stats**: Weapon, Vitality, Spirit. **Flex Slots**: Unlocked by destroying objectives.
* **Beta Status**: Game is in closed-beta. Patches are frequent; prioritize tool data over memory.

**4. Deadlock API**
The Deadlock API is a community-driven, open-source project providing comprehensive game data access.
* **Main Website**: https://deadlock-api.com/
* **Game Data API**: https://api.deadlock-api.com/ — matches, players, leaderboards, hero/item stats, MMR.
  * Swagger Docs: https://api.deadlock-api.com/docs
* **Assets API**: https://assets.deadlock-api.com/ — hero portraits, item icons, ability details, game metadata.
  * Swagger Docs: https://assets.deadlock-api.com/docs
* **GitHub**: https://github.com/deadlock-api/
* **Discord**: https://discord.gg/XMF9Xrgfqu
* **Patreon**: https://www.patreon.com/user?u=68961896
* When users ask about available API endpoints, use `deadlock_api_list_endpoints` to discover endpoints,
  then `deadlock_api_endpoint_details` to get parameter details for specific endpoints.
* Use `deadlock_api_info` for quick resource links and general API information.
</knowledge_base>

<response_formatting>
* **Style**: Short, concise, professional, structural. Use Markdown.
* **Tone**: Warm, confident, never fawning.
</response_formatting>
"""
DEFAULT_MODEL = "claude-sonnet-4-6"

# Default timeout in seconds
DEFAULT_TIMEOUT_SECONDS = 600

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF = 1.0  # seconds
DEFAULT_BACKOFF_MULTIPLIER = 2.0

# Conversation history truncation defaults
DEFAULT_MAX_HISTORY_MESSAGES = 20
DEFAULT_MAX_HISTORY_CHARS = 8000  # ~2000 tokens

# Tool result truncation
MAX_TOOL_RESULT_CHARS = 12000  # ~3000 tokens


class AgentError(Exception):
    """Base exception for agent errors."""


class AgentConfigurationError(AgentError):
    """Raised when agent configuration is invalid."""


class AgentTimeoutError(AgentError):
    """Raised when agent request times out."""


class AgentRetryExhaustedError(AgentError):
    """Raised when all retries have been exhausted."""


class AgentAuthError(AgentError):
    """Raised for authentication errors (non-retriable)."""


class AgentRateLimitError(AgentError):
    """Raised for rate limit errors (non-retriable)."""


@dataclass
class AgentConfig:
    """Configuration for the Claude agent."""

    model: str = DEFAULT_MODEL
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF
    backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER
    max_turns: int = 25  # Allow multiple turns for tool use


# Type alias for SSE callback
type SSEEventCallback = Callable[[ChatToolStartEvent | ChatToolEndEvent | ChatUsageEvent], None]


def _noop_sse_callback(event: ChatToolStartEvent | ChatToolEndEvent | ChatUsageEvent) -> None:
    """No-op SSE callback for when no event emission is needed."""
    pass


def get_agent_config() -> AgentConfig:
    """Get agent configuration from environment variables.

    Returns:
        AgentConfig with values from environment or defaults.
    """
    model = os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
    timeout = float(os.environ.get("AGENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    max_turns = int(os.environ.get("AGENT_MAX_TURNS", 25))
    return AgentConfig(model=model, timeout_seconds=timeout, max_turns=max_turns)


def validate_configuration() -> None:
    """Validate that required configuration is present.

    Supports direct Anthropic API keys, Claude Code OAuth tokens, and
    OpenRouter via ANTHROPIC_AUTH_TOKEN (with ANTHROPIC_API_KEY set to empty string).

    Raises:
        AgentConfigurationError: If no valid authentication is configured.
    """
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_oauth_token = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    has_auth_token = bool(os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if not has_api_key and not has_oauth_token and not has_auth_token:
        raise AgentConfigurationError(
            "ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, or ANTHROPIC_AUTH_TOKEN environment variable is required"
        )


def _is_retriable_error(error: Exception) -> bool:
    """Check if an error is retriable.

    Non-retriable errors:
    - Authentication errors (API key invalid)
    - Rate limit errors

    Args:
        error: The exception to check.

    Returns:
        True if the error is retriable, False otherwise.
    """
    error_str = str(error).lower()

    # Check for authentication errors
    if "authentication" in error_str or "api key" in error_str or "invalid_api_key" in error_str:
        return False

    # Check for rate limit errors
    if "rate limit" in error_str or "rate_limit" in error_str or "too many requests" in error_str:
        return False

    # Check for CLI not found (non-retriable - configuration issue)
    return not isinstance(error, CLINotFoundError)


def _wrap_non_retriable_error(error: Exception) -> AgentError:
    """Wrap non-retriable errors in appropriate exception types.

    Args:
        error: The original exception.

    Returns:
        An AgentError subclass appropriate for the error type.
    """
    error_str = str(error).lower()

    if "authentication" in error_str or "api key" in error_str or "invalid_api_key" in error_str:
        return AgentAuthError(f"Authentication failed: {error}")

    if "rate limit" in error_str or "rate_limit" in error_str or "too many requests" in error_str:
        return AgentRateLimitError(f"Rate limit exceeded: {error}")

    return AgentError(str(error))


@dataclass
class StreamChunk:
    """A chunk of streamed response content."""

    content: str
    is_complete: bool = False


@dataclass
class DeadlockAgentClient:
    """ClaudeSDKClient wrapper for Deadlock AI Assistant.

    Maintains a conversation session across multiple exchanges.
    Uses ClaudeSDKClient for:
    - Session continuity (Claude remembers previous messages)
    - Interrupt support for stopping long operations
    - Proper async context management
    - Custom tool integration via ToolRegistry

    Example:
        async with DeadlockAgentClient() as client:
            async for chunk in client.send_message("Hello"):
                print(chunk.content)

            # Follow-up - Claude remembers context
            async for chunk in client.send_message("What did I just say?"):
                print(chunk.content)

    Example with tools:
        from packages.tools.registry import ToolRegistry

        def sse_callback(event):
            print(f"Tool event: {event}")

        registry = ToolRegistry(sse_callback=sse_callback)
        async with DeadlockAgentClient(tool_registry=registry) as client:
            async for chunk in client.send_message("Search for Abrams on the wiki"):
                print(chunk.content)
    """

    config: AgentConfig = field(default_factory=get_agent_config)
    tool_registry: ToolRegistry | None = field(default=None)
    sse_callback: SSEEventCallback = field(default=_noop_sse_callback)
    _client: ClaudeSDKClient | None = field(default=None, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    _mcp_server: Any = field(default=None, init=False, repr=False)
    _tool_functions: list[Any] = field(default_factory=list, init=False, repr=False)

    async def _create_options(self) -> ClaudeAgentOptions:
        """Create ClaudeAgentOptions from config."""
        # Explicitly disallow file system tools for security
        disallowed_tools = [
            "Read",
            "Write",
            "Edit",
            "Bash",
            "Glob",
            "Grep",
            "NotebookEdit",
            "Task",
        ]

        options_kwargs: dict[str, Any] = {
            "system_prompt": self.config.system_prompt,
            "max_turns": self.config.max_turns,
            "disallowed_tools": disallowed_tools,
            "model": self.config.model if hasattr(self.config, "model") else None,
        }

        # If tool registry is provided, create MCP server with tools
        if self.tool_registry is not None:
            await self._setup_tools()
            if self._mcp_server is not None:
                options_kwargs["mcp_servers"] = {"tools": self._mcp_server}
                # Get tool names for allowed_tools list - only our MCP tools
                tools = await self.tool_registry.get_all_tools()
                allowed_tools = [f"mcp__tools__{name}" for name in tools]
                options_kwargs["allowed_tools"] = allowed_tools
        else:
            # No tools available when no registry is provided
            options_kwargs["allowed_tools"] = []

        return ClaudeAgentOptions(**options_kwargs)

    async def _setup_tools(self) -> None:
        """Set up MCP tools from the tool registry."""
        if self.tool_registry is None:
            return

        tools = await self.tool_registry.get_all_tools()
        tool_definitions = await self.tool_registry.get_tool_definitions()
        self._tool_functions = []

        for tool_def in tool_definitions:
            tool_name = tool_def["name"]
            tool_description = tool_def.get("description", f"Execute {tool_name} tool")
            tool_params = tool_def.get("parameters", {})

            # Create a closure to capture the tool_name
            tool_func = self._create_tool_function(tool_name, tools[tool_name])

            # Decorate with @tool
            decorated = tool(tool_name, tool_description, tool_params)(tool_func)
            self._tool_functions.append(decorated)

        # Create MCP server with all tools
        if self._tool_functions:
            self._mcp_server = create_sdk_mcp_server(
                name="tools",
                version="1.0.0",
                tools=self._tool_functions,
            )

    def _create_tool_function(self, tool_name: str, tool_instance: Any) -> Callable[[dict[str, Any]], Any]:
        """Create a tool function that calls the registry.

        Args:
            tool_name: Name of the tool
            tool_instance: The tool instance from the registry

        Returns:
            An async function that executes the tool
        """

        async def tool_func(args: dict[str, Any]) -> dict[str, Any]:
            """Execute the tool and return MCP-formatted result."""
            try:
                result = await tool_instance.execute(**args)
                # Format result as MCP response using TOON for structured data
                # TOON (Token-Oriented Object Notation) reduces token count by ~40%
                # compared to JSON while maintaining structure clarity for LLMs
                if isinstance(result, str):
                    result_text = result
                elif isinstance(result, (list, dict)):
                    # Use TOON encoding for structured data (lists and dicts)
                    # This provides 30-60% token reduction vs JSON
                    result_text = toon_encode(result)
                else:
                    result_text = str(result)

                # Truncate large tool results to reduce token usage
                if len(result_text) > MAX_TOOL_RESULT_CHARS:
                    original_len = len(result_text)
                    result_text = result_text[:MAX_TOOL_RESULT_CHARS] + (f"\n... [Truncated from {original_len} chars]")

                return {"content": [{"type": "text", "text": result_text}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Error executing {tool_name}: {e}"}], "is_error": True}

        return tool_func

    async def connect(self) -> None:
        """Connect to Claude.

        Creates a new ClaudeSDKClient and establishes a session.
        If a tool_registry is provided, tools are initialized and registered.

        Raises:
            AgentConfigurationError: If required configuration is missing.
        """
        validate_configuration()

        if self._connected:
            return

        options = await self._create_options()
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from Claude.

        Closes the session and releases resources.
        Also closes the tool registry if one was provided.
        """
        if self._client and self._connected:
            await self._client.disconnect()
            self._connected = False
            self._client = None

        # Close tool registry to clean up connections
        if self.tool_registry is not None:
            await self.tool_registry.close()

    async def interrupt(self) -> None:
        """Interrupt current operation.

        Sends an interrupt signal to stop any ongoing task.
        """
        if self._client and self._connected:
            await self._client.interrupt()

    async def __aenter__(self) -> DeadlockAgentClient:
        """Enter async context manager."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: object) -> None:
        """Exit async context manager."""
        await self.disconnect()

    async def send_message(self, message: str) -> AsyncIterator[StreamChunk]:
        """Send a message and stream the response.

        ClaudeSDKClient maintains conversation context automatically,
        so Claude will remember previous messages in this session.

        Args:
            message: The user message to send.

        Yields:
            StreamChunk objects with content and completion status.

        Raises:
            AgentConfigurationError: If not connected.
            AgentTimeoutError: If the request times out.
            AgentRetryExhaustedError: If all retries are exhausted.
            AgentAuthError: If authentication fails.
            AgentRateLimitError: If rate limit is exceeded.
        """
        if not self._client or not self._connected:
            raise AgentConfigurationError("Client not connected. Call connect() first or use async context manager.")

        last_error: Exception | None = None
        backoff = self.config.initial_backoff

        for attempt in range(self.config.max_retries + 1):
            try:
                async for chunk in self._stream_with_timeout(message):
                    yield chunk
                return  # Success
            except TimeoutError as e:
                raise AgentTimeoutError(f"Request timed out after {self.config.timeout_seconds} seconds") from e
            except (ClaudeSDKError, CLIConnectionError, ProcessError) as e:
                if not _is_retriable_error(e):
                    raise _wrap_non_retriable_error(e) from e

                last_error = e
                if attempt < self.config.max_retries:
                    await asyncio.sleep(backoff)
                    backoff *= self.config.backoff_multiplier
                else:
                    raise AgentRetryExhaustedError(
                        f"Failed after {self.config.max_retries + 1} attempts: {last_error}"
                    ) from last_error

    async def _stream_with_timeout(self, message: str) -> AsyncIterator[StreamChunk]:
        """Stream response with timeout.

        Args:
            message: The message to send.

        Yields:
            StreamChunk objects.
        """
        assert self._client is not None  # For type checker

        async def _do_stream() -> AsyncIterator[StreamChunk]:
            assert self._client is not None
            await self._client.query(message)
            async for msg in self._client.receive_response():
                for chunk in self._process_message(msg):
                    yield chunk

        async with asyncio.timeout(self.config.timeout_seconds):
            async for chunk in _do_stream():
                yield chunk

    def _process_message(self, msg: Message) -> list[StreamChunk]:
        """Process a message from the SDK into StreamChunks.

        Also emits SSE events for tool usage via the sse_callback.
        Extracts token usage data from ResultMessage for monitoring.

        Args:
            msg: A message from ClaudeSDKClient.

        Returns:
            List of StreamChunk objects.
        """
        chunks: list[StreamChunk] = []

        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    chunks.append(StreamChunk(content=block.text, is_complete=False))
                elif isinstance(block, ToolUseBlock):
                    # Emit tool start event via SSE callback
                    # Note: The actual tool execution and end events are handled
                    # by the tool itself via the registry's SSE callback
                    pass
        elif isinstance(msg, ResultMessage):
            # Extract token usage data if available
            usage = getattr(msg, "usage", None)
            if usage is not None:
                input_tokens = getattr(usage, "input_tokens", 0)
                output_tokens = getattr(usage, "output_tokens", 0)
                cache_read = getattr(usage, "cache_read_input_tokens", 0)
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
                logger.info(
                    "Token usage: input=%d output=%d cache_read=%d cache_creation=%d",
                    input_tokens,
                    output_tokens,
                    cache_read,
                    cache_creation,
                )
                usage_event = ChatUsageEvent(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_creation,
                )
                self.sse_callback(usage_event)
            chunks.append(StreamChunk(content="", is_complete=True))

        return chunks


# -----------------------------------------------------------------------------
# Legacy API - Backward compatible functions using ClaudeSDKClient internally
# -----------------------------------------------------------------------------


async def stream_response(
    message: str,
    config: AgentConfig | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    tool_registry: ToolRegistry | None = None,
    sse_callback: SSEEventCallback | None = None,
) -> AsyncIterator[StreamChunk]:
    """Stream a response from the Claude agent.

    Note: For multi-turn conversations with context preservation, prefer using
    DeadlockAgentClient directly. This function creates a new session per call.

    Args:
        message: The user message to send.
        config: Optional agent configuration. Uses defaults if not provided.
        conversation_history: Optional list of previous messages as dicts with 'role' and 'content'.
            When provided, history is formatted into the prompt for context.
        tool_registry: Optional tool registry for custom tool integration.
        sse_callback: Optional callback for SSE tool events.

    Yields:
        StreamChunk objects with content and completion status.

    Raises:
        AgentConfigurationError: If required configuration is missing.
        AgentTimeoutError: If the request times out.
        AgentRetryExhaustedError: If all retries are exhausted.
        AgentAuthError: If authentication fails.
        AgentRateLimitError: If rate limit is exceeded.
    """
    validate_configuration()

    if config is None:
        config = get_agent_config()

    # Build the full prompt with conversation history for single-session usage
    full_prompt = _build_prompt_with_history(message, conversation_history)

    # Use the provided SSE callback or a no-op callback
    callback = sse_callback if sse_callback is not None else _noop_sse_callback

    # Use ClaudeSDKClient with async context manager for proper cleanup
    async with DeadlockAgentClient(config=config, tool_registry=tool_registry, sse_callback=callback) as client:
        async for chunk in client.send_message(full_prompt):
            yield chunk


def _build_prompt_with_history(
    message: str,
    conversation_history: list[dict[str, str]] | None,
) -> str:
    """Build a prompt that includes conversation history.

    Applies sliding window truncation to keep token usage bounded:
    - Keeps at most MAX_HISTORY_MESSAGES recent messages
    - Truncates oldest messages if total chars exceed MAX_HISTORY_CHARS
    - Prepends "[Earlier messages omitted]" when truncation occurs

    Args:
        message: The current user message.
        conversation_history: Previous conversation messages.

    Returns:
        A formatted prompt string.
    """
    if not conversation_history:
        return message

    max_messages = int(os.environ.get("AGENT_MAX_HISTORY_MESSAGES", DEFAULT_MAX_HISTORY_MESSAGES))
    max_chars = int(os.environ.get("AGENT_MAX_HISTORY_CHARS", DEFAULT_MAX_HISTORY_CHARS))

    # Apply sliding window: keep last N messages
    truncated = len(conversation_history) > max_messages
    history = conversation_history[-max_messages:]

    # Format conversation history as context
    history_parts: list[str] = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            history_parts.append(f"User: {content}")
        elif role == "assistant":
            history_parts.append(f"Assistant: {content}")

    # Truncate oldest messages if over char budget
    total_chars = sum(len(part) for part in history_parts)
    while history_parts and total_chars > max_chars:
        removed = history_parts.pop(0)
        total_chars -= len(removed)
        truncated = True

    if history_parts:
        prefix = "[Earlier messages omitted]\n" if truncated else ""
        history_text = "\n".join(history_parts)
        return f"Previous conversation:\n{prefix}{history_text}\n\nUser: {message}"

    return message


async def get_response(
    message: str,
    config: AgentConfig | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Get a complete response from the Claude agent.

    This is a convenience method that collects all streamed chunks into a single response.

    Args:
        message: The user message to send.
        config: Optional agent configuration.
        conversation_history: Optional conversation history.

    Returns:
        The complete response text.

    Raises:
        AgentConfigurationError: If required configuration is missing.
        AgentTimeoutError: If the request times out.
        AgentRetryExhaustedError: If all retries are exhausted.
        AgentAuthError: If authentication fails.
        AgentRateLimitError: If rate limit is exceeded.
    """
    response_parts: list[str] = []

    async for chunk in stream_response(message, config, conversation_history):
        if chunk.content:
            response_parts.append(chunk.content)

    return "".join(response_parts)
