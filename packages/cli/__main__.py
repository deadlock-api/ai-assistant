"""Deadlock AI Assistant - CLI entry point.

Provides two modes:
- Default: Simple console CLI with logging
- --tui: Full Textual TUI application
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.tools.registry import ToolLoadWarning, ToolRegistry

# Configure logging for console mode
logger = logging.getLogger("deadlock_assistant")


def setup_logging(level: int = logging.DEBUG) -> None:
    """Configure logging to show debug and info messages."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def run_console_cli() -> None:
    """Run the simple console CLI with logging."""
    from packages.ai_assistant.agent import (
        AgentAuthError,
        AgentConfigurationError,
        AgentError,
        AgentRateLimitError,
        AgentRetryExhaustedError,
        AgentTimeoutError,
        stream_response,
    )
    from packages.tools.registry import ToolRegistry

    # Initialize tool registry
    logger.info("Initializing tools...")

    def log_sse_callback(event: object) -> None:
        logger.debug(f"Tool event: {event}")

    tool_registry: ToolRegistry | None = None
    try:
        tool_registry = ToolRegistry(sse_callback=log_sse_callback)
        await tool_registry.get_all_tools()
        warnings = tool_registry.get_warnings()
        for warning in warnings:
            logger.warning(f"{warning.toolset} tools failed to load: {warning.message}")
        logger.info("Tools initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize tool registry: {e}")

    # Conversation history
    conversation_history: list[dict[str, str]] = []

    print("\nDeadlock Assistant - Console Mode")
    print("Type 'quit' or 'exit' to quit, 'clear' to clear history\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        if user_input.lower() == "clear":
            conversation_history.clear()
            print("Conversation history cleared.\n")
            continue

        logger.debug(f"Sending message: {user_input[:50]}...")

        # Stream the response
        response_parts: list[str] = []
        error_occurred = False

        print("Assistant: ", end="", flush=True)

        try:
            async for chunk in stream_response(
                user_input,
                conversation_history=conversation_history,
                tool_registry=tool_registry,
                sse_callback=log_sse_callback,
            ):
                if chunk.content:
                    response_parts.append(chunk.content)
                    print(chunk.content, end="", flush=True)
        except AgentConfigurationError as e:
            print(f"\n[Error: Configuration error - {e}]")
            error_occurred = True
        except AgentTimeoutError:
            print("\n[Error: Request timed out]")
            error_occurred = True
        except AgentAuthError:
            print("\n[Error: Authentication failed]")
            error_occurred = True
        except AgentRateLimitError:
            print("\n[Error: Rate limit exceeded]")
            error_occurred = True
        except AgentRetryExhaustedError:
            print("\n[Error: Failed after multiple retries]")
            error_occurred = True
        except AgentError as e:
            print(f"\n[Error: {e}]")
            error_occurred = True

        print("\n")  # New line after response

        # Save to history if no error
        if not error_occurred:
            conversation_history.append({"role": "user", "content": user_input})
            full_response = "".join(response_parts)
            if full_response:
                conversation_history.append({"role": "assistant", "content": full_response})
                logger.debug(f"Response length: {len(full_response)} chars")

    # Cleanup
    if tool_registry is not None:
        await tool_registry.close()


def run_tui() -> None:
    """Run the Textual TUI application."""
    from textual.app import App, ComposeResult
    from textual.containers import Container, VerticalScroll
    from textual.widgets import Footer, Header, Input, Static

    class ConnectionStatus(Static):
        """Widget to display connection status in the header."""

        DEFAULT_CSS = """
        ConnectionStatus {
            dock: right;
            width: auto;
            padding: 0 1;
        }
        """

        def __init__(self) -> None:
            super().__init__("● Connected")

    class WarningMessage(Static):
        """Widget to display a warning message."""

        DEFAULT_CSS = """
        WarningMessage {
            padding: 0 1;
            margin: 1 0;
            color: $warning;
            background: $warning 10%;
        }
        """

    class UserMessage(Static):
        """Widget to display a user message."""

        DEFAULT_CSS = """
        UserMessage {
            padding: 0 1;
            margin: 1 0;
            color: $success;
        }
        """

    class AssistantMessage(Static):
        """Widget to display an assistant message with streaming support."""

        DEFAULT_CSS = """
        AssistantMessage {
            padding: 0 1;
            margin: 1 0;
            color: $secondary;
        }
        """

        def __init__(self, initial_content: str = "") -> None:
            super().__init__(initial_content)
            self._content = initial_content

        def append_content(self, content: str) -> None:
            """Append content to the message (for streaming)."""
            self._content += content
            self.update(self._content)

    class ConversationOutput(VerticalScroll):
        """Scrollable container for conversation history."""

        DEFAULT_CSS = """
        ConversationOutput {
            height: 1fr;
            border: solid $primary;
            padding: 1;
        }
        """

    class InputArea(Container):
        """Fixed input area at the bottom of the screen."""

        DEFAULT_CSS = """
        InputArea {
            height: auto;
            max-height: 5;
            dock: bottom;
            padding: 0 1;
        }
        """

        def compose(self) -> ComposeResult:
            """Create the input widget."""
            yield Input(placeholder="Type your message here...", id="message-input")

    class DeadlockAssistantApp(App):
        """Deadlock AI Assistant TUI application."""

        TITLE = "Deadlock Assistant"

        def __init__(self) -> None:
            """Initialize the app with in-memory conversation storage."""
            super().__init__()
            self._conversation_history: list[dict[str, str]] = []
            self._tool_registry: ToolRegistry | None = None

        CSS = """
        Screen {
            layout: grid;
            grid-size: 1;
            grid-rows: auto 1fr auto;
        }

        Header {
            height: auto;
        }

        #main-content {
            height: 1fr;
            min-height: 80%;
        }

        Footer {
            height: auto;
        }
        """

        BINDINGS = [
            ("ctrl+c", "quit", "Quit"),
            ("ctrl+q", "quit", "Quit"),
            ("ctrl+l", "clear", "Clear"),
            ("escape", "clear_input", "Clear Input"),
        ]

        def compose(self) -> ComposeResult:
            """Create child widgets for the app."""
            yield Header()
            yield ConnectionStatus()
            yield Container(
                ConversationOutput(id="conversation-output"),
                InputArea(),
                id="main-content",
            )
            yield Footer()

        def on_mount(self) -> None:
            """Focus the input field and initialize tools when the app starts."""
            self.query_one("#message-input", Input).focus()
            self.run_worker(self._initialize_tools(), exclusive=True)

        async def _initialize_tools(self) -> None:
            """Initialize the tool registry and display any warnings."""
            from packages.tools.registry import ToolRegistry

            def noop_sse_callback(event: object) -> None:
                pass

            try:
                self._tool_registry = ToolRegistry(sse_callback=noop_sse_callback)
                await self._tool_registry.get_all_tools()
                warnings = self._tool_registry.get_warnings()

                if warnings:
                    await self._display_tool_warnings(warnings)
            except Exception as e:
                print(f"Warning: Failed to initialize tool registry: {e}", file=sys.stderr)
                output = self.query_one("#conversation-output", ConversationOutput)
                warning_msg = WarningMessage(f"Warning: Failed to initialize tools: {e}")
                await output.mount(warning_msg)

        async def _display_tool_warnings(self, warnings: list[ToolLoadWarning]) -> None:
            """Display tool loading warnings in the TUI and print to stderr."""
            output = self.query_one("#conversation-output", ConversationOutput)

            for warning in warnings:
                msg = f"Warning: {warning.toolset} tools failed to load: {warning.message}"
                print(msg, file=sys.stderr)
                warning_widget = WarningMessage(msg)
                await output.mount(warning_widget)

        def action_clear(self) -> None:
            """Clear the conversation display and history (Ctrl+L)."""
            output = self.query_one("#conversation-output", ConversationOutput)
            output.remove_children()
            self._conversation_history.clear()

        def action_clear_input(self) -> None:
            """Clear the input field (Escape)."""
            input_widget = self.query_one("#message-input", Input)
            input_widget.value = ""

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            """Handle message submission when Enter is pressed."""
            message = event.value.strip()
            if not message:
                return

            input_widget = event.input
            input_widget.value = ""
            input_widget.disabled = True

            output = self.query_one("#conversation-output", ConversationOutput)

            user_msg = UserMessage(f"You: {message}")
            await output.mount(user_msg)

            assistant_msg = AssistantMessage("Assistant: ")
            await output.mount(assistant_msg)

            output.scroll_end(animate=False)

            try:
                await self._stream_assistant_response(message, assistant_msg, output)
            finally:
                input_widget.disabled = False
                input_widget.focus()

        async def _stream_assistant_response(
            self,
            message: str,
            assistant_widget: AssistantMessage,
            output: ConversationOutput,
        ) -> None:
            """Stream the assistant response token by token."""
            from packages.ai_assistant.agent import (
                AgentAuthError,
                AgentConfigurationError,
                AgentError,
                AgentRateLimitError,
                AgentRetryExhaustedError,
                AgentTimeoutError,
                stream_response,
            )

            response_parts: list[str] = []
            error_occurred = False

            try:
                async for chunk in stream_response(
                    message,
                    conversation_history=self._conversation_history,
                    tool_registry=self._tool_registry,
                ):
                    if chunk.content:
                        response_parts.append(chunk.content)
                        assistant_widget.append_content(chunk.content)
                        output.scroll_end(animate=False)
            except AgentConfigurationError as e:
                assistant_widget.append_content(f"\n[Error: Configuration error - {e}]")
                error_occurred = True
            except AgentTimeoutError:
                assistant_widget.append_content("\n[Error: Request timed out]")
                error_occurred = True
            except AgentAuthError:
                assistant_widget.append_content("\n[Error: Authentication failed]")
                error_occurred = True
            except AgentRateLimitError:
                assistant_widget.append_content("\n[Error: Rate limit exceeded]")
                error_occurred = True
            except AgentRetryExhaustedError:
                assistant_widget.append_content("\n[Error: Failed after multiple retries]")
                error_occurred = True
            except AgentError as e:
                assistant_widget.append_content(f"\n[Error: {e}]")
                error_occurred = True

            if not error_occurred:
                self._conversation_history.append({"role": "user", "content": message})
                full_response = "".join(response_parts)
                if full_response:
                    self._conversation_history.append({"role": "assistant", "content": full_response})

    # Run the TUI app
    app = DeadlockAssistantApp()
    app.run()


def validate_configuration() -> str | None:
    """Validate required environment variables.

    Returns:
        Error message if validation fails, None if successful.
    """
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return (
            "Error: ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN environment variable is required.\n\n"
            "Please set one before running the assistant:\n"
            "  export ANTHROPIC_API_KEY=your-api-key\n"
            "  or\n"
            "  export CLAUDE_CODE_OAUTH_TOKEN=your-oauth-token"
        )
    return None


def main() -> None:
    """Entry point for the deadlock-assistant CLI."""
    parser = argparse.ArgumentParser(
        description="Deadlock AI Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch the full Textual TUI interface",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (debug) logging",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only show warnings and errors",
    )

    args = parser.parse_args()

    # Validate configuration
    error = validate_configuration()
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)

    if args.tui:
        # Run the TUI (no logging setup needed, Textual handles its own output)
        run_tui()
    else:
        # Set up logging for console mode
        if args.quiet:
            setup_logging(logging.WARNING)
        elif args.verbose:
            setup_logging(logging.DEBUG)
        else:
            setup_logging(logging.INFO)

        # Run the console CLI
        asyncio.run(run_console_cli())


if __name__ == "__main__":
    main()
