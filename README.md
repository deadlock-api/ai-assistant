# Deadlock AI-Assistant

A comprehensive AI assistant for analyzing Deadlock game data using advanced language models and ClickHouse database querying capabilities. Optimized for Discord bot integration with memory-based conversation management and concurrent user support.

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
│   ├── tests/            # Test suite
│   ├── api.py            # FastAPI web service
│   ├── cli.py            # Command-line interface
│   ├── configs.py        # Configuration management
│   ├── message_store.py  # Memory persistence layer
│   ├── tools.py          # Custom tools and functions
│   └── utils.py          # Utility functions
├── docker-compose.yaml   # Docker orchestration
├── Dockerfile           # Container configuration
├── pyproject.toml       # Project configuration
└── README.md           # This file
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
uv run pre-commit install
```

## Model Configuration

The application supports multiple language model providers with runtime selection capabilities. Configuration requires selecting an appropriate model and establishing authentication credentials.

### Available Model Options

The application includes support for the following model providers:

- **Hugging Face Inference API** (Default - Recommended for new users)
- **Google Gemini Models** (Multiple variants: Flash, Flash-Lite, Pro)
- **Ollama Local Models** (Requires local Ollama installation)

### Hugging Face Setup (Default Configuration)

Hugging Face provides the most straightforward configuration process and is set as the default model provider.

#### Token Generation

Navigate to your Hugging Face account settings at https://huggingface.co/settings/tokens and create a new User Access Token with Read permissions. Provide a descriptive name such as "Deadlock AI Assistant Development" for future reference.

#### Environment Variable Configuration

Configure your Hugging Face token using one of the following methods:

**Project-Specific Configuration (Recommended)**
Create a `.env` file in the project root directory with the following content:

```env
HF_TOKEN=your_token_here
MODEL=hf
```

**Session-Based Configuration (Temporary)**
Execute the following command in PowerShell before running the application:

```powershell
$env:HF_TOKEN="your_token_here"
```

### Google Gemini Setup

For Google Gemini models, obtain an API key from Google AI Studio and configure it:

```env
GEMINI_API_KEY=your_api_key_here
MODEL=gemini-flash
```

Available Gemini models:

- `gemini-flash-lite` - Fastest, most efficient
- `gemini-flash` - Balanced performance
- `gemini-pro` - Highest capability

### Ollama Local Setup

For local model inference, install Ollama and configure:

```env
MODEL=ollama
```

## Application Execution

### Command Line Interface

Execute the application in interactive console mode from the project root directory:

```bash
uv run python -m ai_assistant.cli
```

This provides a simple command-line interface for testing queries directly.

### Web Service Mode

Launch the application as a web service using the API module:

```bash
uv run python -m ai_assistant.api
```

The web service provides API endpoints accessible at http://localhost:8000, with interactive documentation available at http://localhost:8000/scalar.

### Docker Deployment

For production deployment, use Docker Compose:

```bash
docker-compose up -d
```

This includes Redis for persistent memory storage and proper container orchestration.

## API Usage

The web service provides memory-based conversation management with support for multiple concurrent users, making it ideal for Discord bot integration.

### Authentication

If the `API_KEYS` environment variable is set, all requests require authentication:

```env
API_KEYS=your-api-key-1,your-api-key-2
```

Include the API key in requests:

```bash
POST /invoke?api_key=your-api-key&prompt=your-query
```

### Core Endpoints

#### **GET /invoke** - Main Conversation Interface

Submit prompts to the AI assistant with automatic memory management.

**Parameters:**

- `prompt` (required): The question or command to send to the AI assistant (1-10000 characters)
- `memory_id` (optional): UUID for conversation continuity
- `model` (optional): Model to use for inference (default: configured model)
- `api_key` (optional): Authentication key if required

**Usage Examples:**

**Basic Query:**

```bash
GET /invoke?prompt=How many heroes are available in Deadlock?
```

**Continuing Conversation:**

```bash
GET /invoke?memory_id=d450bc53-9b2c-42af-bd29-5ba0ce57184c&prompt=Tell me more about the strongest ones
```

**Model Selection:**

```bash
GET /invoke?prompt=Analyze johnpyp's match performance&model=gemini-pro
```

#### **GET /replay** - Demo Response

Provides a sample streaming response for testing and demonstration purposes.

**Parameters:**

- `prompt` (required): Prompt text (for compatibility)
- `memory_id` (optional): Memory ID (for compatibility)
- `model` (optional): Model selection (for compatibility)
- `sleep_time` (optional): Delay between response chunks in seconds

#### **GET /scalar** - Interactive Documentation

Access comprehensive API documentation with interactive testing capabilities.

### Response Format

All streaming responses follow a structured Server-Sent Events format:

```
event: agentStep
data: {"type": "action|planning|delta|final_answer|error", "data": {...}}

event: memoryId
data: uuid-string
```

**Response Types:**

- `action`: Tool execution and function calls
- `planning`: AI reasoning and decision-making steps
- `delta`: Incremental response content
- `final_answer`: Complete response data
- `error`: Error messages and troubleshooting information

### Memory Management

The system automatically manages conversation memory:

- Each conversation receives a unique memory ID upon completion
- Memory IDs can be reused to continue conversations
- Redis storage provides persistence across restarts
- In-memory fallback when Redis is unavailable

**Key Benefits for Discord Bots:**

- Persistent conversation memory across bot restarts
- Individual memory management per Discord user
- Graceful error handling prevents bot crashes
- Multiple model selection for different use cases

## Environment Configuration

### Required Environment Variables

Create a `.env` file with the following configuration:

```env
# Model Configuration (choose one)
HF_TOKEN=your_huggingface_token
# OR
GEMINI_API_KEY=your_gemini_api_key

# Optional: Force specific model
MODEL=hf

# Optional: API Authentication
API_KEYS=your-api-key-1,your-api-key-2

# Optional: Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password
```

### Model Selection Priority

The application selects models in the following order:

1. `MODEL` environment variable (if set)
2. Google Gemini (if `GEMINI_API_KEY` is available)
3. Hugging Face (if `HF_TOKEN` is available)
4. Error if no valid configuration found

## Testing

Run the test suite to ensure proper functionality:

```bash
uv run pytest ai_assistant/tests/
```

Tests cover:

- Tool functionality and API integration
- Message store operations (both Redis and in-memory)
- Core application logic

## Common Troubleshooting

### Python Command Issues on Windows

Windows systems typically use `python` rather than `python3` as the executable name. If you encounter "Python was not found" errors, ensure you are using the correct command syntax for your operating system.

### Module Import Errors

Import errors typically indicate execution from an incorrect directory. Ensure all commands are executed from the project root directory containing `pyproject.toml`, not from within the `ai_assistant` subdirectory.

### Model Connection Failures

Connection errors usually result from missing or incorrect API credentials. Verify that your environment variables are properly configured and that your chosen model provider credentials are valid.

### Memory Persistence Issues

If conversations aren't persisting:

- Check Redis connectivity if using Redis storage
- Verify memory IDs are being returned and stored properly
- Monitor logs for memory storage errors

### API Authentication Errors

If receiving 401 Unauthorized errors:

- Ensure `API_KEYS` environment variable matches request parameter
- Verify API key format and validity
- Check that authentication is required (API_KEYS is set)

### Docker Deployment Issues

For container-related problems:

- Ensure Docker and Docker Compose are installed
- Verify environment variables are passed to containers
- Check container logs for specific error messages

## Architecture Overview

The application uses a memory-based architecture that provides:

- **Memory Persistence**: Conversations maintain context across requests
- **Concurrency Safety**: Multiple users can interact simultaneously
- **Storage Flexibility**: Redis or in-memory storage options
- **Model Agnostic**: Runtime model selection and switching
- **Production Ready**: Docker deployment with proper orchestration
- **Development Friendly**: Local testing with replay functionality

## Development Guidelines

Maintain code quality by ensuring pre-commit hooks execute successfully before committing changes. The hooks perform automated formatting, linting, and testing to maintain consistency across the codebase.

For additional development resources and advanced configuration options, consult the project documentation and dependency specifications within `pyproject.toml`.

## API Reference Summary

| Endpoint  | Method | Description                    |
| --------- | ------ | ------------------------------ |
| `/invoke` | GET    | Main AI conversation interface |
| `/replay` | GET    | Demo streaming response        |
| `/scalar` | GET    | Interactive API documentation  |
| `/`       | GET    | Redirect to documentation      |

All endpoints support CORS and return Server-Sent Events for real-time streaming responses.
