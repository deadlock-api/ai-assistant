# Deadlock AI-Assistant

A comprehensive AI assistant for analyzing Deadlock game data using advanced language models and database querying capabilities.

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

The application supports multiple language model providers. Configuration requires selecting an appropriate model and establishing authentication credentials.

### Available Model Options

The application includes support for the following model providers:

- **Hugging Face Inference API** (Recommended for new users)
- **Google Gemini Models** (Requires Google Cloud credentials)
- **Ollama Local Models** (Requires local Ollama installation)

### Hugging Face Setup (Recommended)

Hugging Face provides the most straightforward configuration process for new developers.

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

#### Model Selection

Modify the model selection in `ai_assistant/__main__.py` by changing line 28 from:

```python
}["ollama"]
```

to:

```python
}["hf"]
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

## Common Troubleshooting

### Python Command Issues on Windows

Windows systems typically use `python` rather than `python3` as the executable name. If you encounter "Python was not found" errors, ensure you are using the correct command syntax for your operating system.

### Module Import Errors

Import errors typically indicate execution from an incorrect directory. Ensure all commands are executed from the project root directory containing `pyproject.toml`, not from within the `ai_assistant` subdirectory.

### Model Connection Failures

Connection errors usually result from missing or incorrect API credentials. Verify that your environment variables are properly configured and that your chosen model provider credentials are valid.

### App Execution Alias Conflicts

Windows may redirect Python commands to the Microsoft Store. Disable these redirects by navigating to Settings → Apps → Advanced app settings → App execution aliases and disabling the toggles for "python.exe" and "python3.exe".

## API Usage

The web service provides the following endpoints:

- `GET /health` - Service health verification
- `GET /invoke?prompt=your_question` - AI assistant query interface
- `GET /scalar` - Interactive API documentation

Query the assistant by accessing the invoke endpoint with your question as a URL parameter. The service returns streaming responses for real-time interaction.

## Development Guidelines

Maintain code quality by ensuring pre-commit hooks execute successfully before committing changes. The hooks perform automated formatting, linting, and testing to maintain consistency across the codebase.

For additional development resources and advanced configuration options, consult the project documentation and dependency specifications within `pyproject.toml`.
