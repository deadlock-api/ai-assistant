# Deadlock AI-Assistant

A comprehensive AI assistant for analyzing Deadlock game data using advanced language models and database querying capabilities. Optimized for Discord bot integration with session-based conversation management and concurrent user support.

## Prerequisites

Before beginning the installation process, ensure your development environment meets the following requirements:

### Python Installation

This project requires Python 3.11 or higher. Verify your Python installation by executing the following command in your terminal:

```bash
python --version
```

If Python is not installed or the version is insufficient, download the latest version from the official Python website at https://python.org. During installation on Windows systems, ensure you select the option to "Add Python to PATH" to enable command-line access.

### Package Manager Installation

Install UV, the modern Python package manager, from https://docs.astral.sh/uv/getting-started/installation/. UV provides faster dependency resolution and improved project management compared to traditional pip installations.

## Installation Process

### Step 1: Project Setup

Clone the repository and navigate to the project root directory. The project structure should appear as follows:

```
ai-assistant/
├── ai_assistant/          # Main package directory
│   ├── __main__.py       # Application entry point
│   ├── tools.py          # Custom tools and functions
│   └── utils.py          # Utility functions
├── pyproject.toml        # Project configuration
└── README.md            # This file
```

### Step 2: Dependency Installation

From the project root directory, install all required dependencies using UV:

```bash
uv sync
```

This command creates a virtual environment and installs all packages specified in the project configuration.

### Step 3: Pre-commit Hook Configuration

Install the pre-commit hook to ensure code quality standards:

```bash
pre-commit install
```

## Model Configuration

The application supports multiple language model providers with runtime selection capabilities. Configuration requires selecting an appropriate model and establishing authentication credentials.

### Available Model Options

The application includes support for the following model providers:

- **Hugging Face Inference API** (Default - Recommended for new users)
- **Google Gemini Models** (Requires Google Cloud credentials)
- **Ollama Local Models** (Requires local Ollama installation)

### Hugging Face Setup (Default Configuration)

Hugging Face provides the most straightforward configuration process and is set as the default model provider.

#### Token Generation

Navigate to your Hugging Face account settings at https://huggingface.co/settings/tokens and create a new User Access Token with Read permissions. Provide a descriptive name such as "Deadlock AI Assistant Development" for future reference.

#### Environment Variable Configuration

Configure your Hugging Face token using one of the following methods:

**Windows System Environment Variables (Permanent)**

1. Open System Properties through Control Panel
2. Navigate to Advanced system settings
3. Select Environment Variables
4. Add a new user variable named `HF_TOKEN` with your token value

**Project-Specific Configuration**
Create a `.env` file in the project root directory with the following content:

```
HF_TOKEN=your_token_here
```

**Session-Based Configuration (Temporary)**
Execute the following command in PowerShell before running the application:

```powershell
$env:HF_TOKEN="your_token_here"
```

## Application Execution

### Console Mode

Execute the application in interactive console mode from the project root directory:

```bash
uv run python -m ai_assistant
```

**Important**: Always run this command from the project root directory, not from within the `ai_assistant` subdirectory. The application requires proper Python module path resolution to locate package imports correctly.

### Web Service Mode

Launch the application as a web service using Uvicorn:

```bash
uv run uvicorn ai_assistant.__main__:app --reload
```

The web service provides API endpoints accessible at http://localhost:8000, with interactive documentation available at http://localhost:8000/scalar.

## API Usage

The web service provides session-based conversation management with support for multiple concurrent users, making it ideal for Discord bot integration.

### Core Endpoints

#### **POST /invoke** - Main Conversation Interface

Submit prompts to the AI assistant with automatic session management.

**Parameters:**

- `prompt` (required): The question or command to send to the AI assistant (1-10000 characters)
- `user_id` (optional): Discord user ID or custom identifier for automatic session isolation
- `session_id` (optional): Override session ID for development and testing purposes
- `model` (optional): Model to use for inference (default: "hf")

**Usage Examples:**

**Discord Bot Integration:**

```bash
POST /invoke?user_id=discord_123456789&prompt=analyze my steam profile&model=gemini-pro
```

**Development Testing:**

```bash
POST /invoke?session_id=test-session&prompt=what deadlock heroes are available?
```

**Anonymous Usage:**

```bash
POST /invoke?prompt=query the database for match statistics
```

#### **GET /models** - Available Models

List all available language models and the current default.

**Response:**

```json
{
  "models": ["hf", "gemini-flash", "gemini-pro", "ollama"],
  "default": "hf"
}
```

### Session Management

#### **POST /sessions/new** - Create New Session

Pre-create a session with a specific model configuration.

**Parameters:**

- `user_id` (optional): Discord user ID or custom identifier
- `model` (optional): Model to use for this session (default: "hf")

**Response:**

```json
{
  "session_id": "uuid-or-user-id",
  "model": "hf"
}
```

#### **GET /sessions** - List Active Sessions

View all currently active conversation sessions.

**Response:**

```json
{
  "sessions": {
    "discord_123456789": {
      "model": "gemini-pro",
      "active": true
    },
    "test-session": {
      "model": "hf",
      "active": true
    }
  }
}
```

#### **DELETE /sessions/{session_id}** - Clear Session

Remove a specific conversation session and its context.

**Response:**

```json
{
  "message": "Session {session_id} cleared"
}
```

### Utility Endpoints

- **GET /health** - Service health verification
- **GET /scalar** - Interactive API documentation
- **GET /** - Redirect to documentation

### Response Format

All streaming responses follow a structured format for reliable parsing:

```json
{
  "type": "session_info|action|planning|delta|final_answer|error",
  "data": {}
}
```

**Response Types:**

- `session_info`: Contains the session ID being used
- `action`: Tool execution and function calls
- `planning`: AI reasoning and decision-making steps
- `delta`: Incremental response content
- `final_answer`: Complete response data
- `error`: Error messages and troubleshooting information

### Discord Bot Integration

The API is specifically optimized for Discord bot usage with automatic user isolation:

```python
import asyncio
import aiohttp

async def handle_discord_message(user_id: str, message: str):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            'http://localhost:8000/invoke',
            params={
                'user_id': user_id,           # Automatic session per Discord user
                'prompt': message,
                'model': 'gemini-pro'         # Optional model selection
            }
        ) as response:
            async for line in response.content:
                if line.startswith(b'data: '):
                    data = json.loads(line[6:])
                    if data['type'] == 'final_answer':
                        return data['data']
```

**Key Benefits for Discord Bots:**

- Each Discord user maintains separate conversation context
- Multiple users can chat simultaneously without interference
- Automatic session management eliminates manual session handling
- Graceful error handling prevents bot crashes

## Common Troubleshooting

### Python Command Issues on Windows

Windows systems typically use `python` rather than `python3` as the executable name. If you encounter "Python was not found" errors, ensure you are using the correct command syntax for your operating system.

### Module Import Errors

Import errors typically indicate execution from an incorrect directory. Ensure all commands are executed from the project root directory containing `pyproject.toml`, not from within the `ai_assistant` subdirectory.

### Model Connection Failures

Connection errors usually result from missing or incorrect API credentials. Verify that your environment variables are properly configured and that your chosen model provider credentials are valid. Check available models using the `/models` endpoint.

### Session Management Issues

If sessions behave unexpectedly, use the `/sessions` endpoint to view active sessions and `/sessions/{session_id}` DELETE endpoint to clear problematic sessions.

### App Execution Alias Conflicts

Windows may redirect Python commands to the Microsoft Store. Disable these redirects by navigating to Settings → Apps → Advanced app settings → App execution aliases and disabling the toggles for "python.exe" and "python3.exe".

## Architecture Overview

The application uses a session-based architecture that provides:

- **Concurrency Safety**: Multiple users can interact simultaneously without context pollution
- **Memory Management**: Sessions can be individually created and destroyed
- **Model Flexibility**: Runtime model selection per session
- **Discord Optimization**: Automatic user isolation using Discord user IDs
- **Development Support**: Manual session override for testing and debugging

## Development Guidelines

Maintain code quality by ensuring pre-commit hooks execute successfully before committing changes. The hooks perform automated formatting, linting, and testing to maintain consistency across the codebase.

For additional development resources and advanced configuration options, consult the project documentation and dependency specifications within `pyproject.toml`.
