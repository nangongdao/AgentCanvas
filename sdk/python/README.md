# AgentCanvas Python SDK

Official Python SDK for [AgentCanvas](https://github.com/yourusername/agentcanvas) - A visual workflow automation platform powered by LLMs.

## Features

- 🔄 **Workflow Management**: Create, read, update, and delete workflows
- ▶️ **Execution Control**: Start, monitor, and cancel workflow executions
- 🔌 **Provider Management**: Manage LLM providers (OpenAI, Anthropic, etc.)
- 🛡️ **Type Safety**: Full Pydantic model validation
- 🚨 **Error Handling**: Comprehensive exception hierarchy
- 📦 **Context Manager**: Automatic resource cleanup
- 📄 **Pagination**: Built-in pagination support

## Installation

```bash
pip install agentcanvas
```

## Quick Start

```python
from agentcanvas import AgentCanvasClient, CreateWorkflowInput

# Initialize client
client = AgentCanvasClient(
    api_key="your-api-key",
    base_url="http://localhost:8000"
)

# Create a workflow
workflow = client.create_workflow(
    CreateWorkflowInput(
        name="My Workflow",
        description="Automated data processing",
        nodes=[
            {
                "id": "start",
                "type": "start",
                "position": {"x": 100, "y": 100},
                "data": {"label": "Start"}
            }
        ],
        edges=[]
    )
)

# Start execution
from agentcanvas import StartExecutionInput

execution = client.start_execution(
    StartExecutionInput(
        workflow_id=workflow.id,
        inputs={"query": "Process this data"}
    )
)

print(f"Execution started: {execution.id}")
print(f"Status: {execution.status}")
```

## Usage

### Context Manager

```python
with AgentCanvasClient(api_key="your-key") as client:
    workflows = client.list_workflows()
    # Client automatically closes
```

### Workflow Operations

```python
# List workflows with pagination
from agentcanvas import PaginationParams

workflows = client.list_workflows(
    PaginationParams(skip=0, limit=10)
)

# Get specific workflow
workflow = client.get_workflow(workflow_id)

# Update workflow
from agentcanvas import UpdateWorkflowInput

updated = client.update_workflow(
    workflow_id,
    UpdateWorkflowInput(name="Updated Name")
)

# Delete workflow
client.delete_workflow(workflow_id)
```

### Execution Operations

```python
# List executions
executions = client.list_executions()

# Get execution details
execution = client.get_execution(execution_id)

# Cancel execution
client.cancel_execution(execution_id)
```

### Provider Operations

```python
from agentcanvas import CreateProviderInput

# Create provider
provider = client.create_provider(
    CreateProviderInput(
        name="OpenAI",
        type="openai",
        api_key="sk-...",
        models=["gpt-4", "gpt-3.5-turbo"]
    )
)

# List providers
providers = client.list_providers()

# Update provider
from agentcanvas import UpdateProviderInput

client.update_provider(
    provider.id,
    UpdateProviderInput(models=["gpt-4"])
)

# Delete provider
client.delete_provider(provider.id)
```

### Error Handling

```python
from agentcanvas import (
    NotFoundError,
    AuthenticationError,
    ValidationError,
    RateLimitError,
    TimeoutError
)

try:
    workflow = client.get_workflow("invalid-id")
except NotFoundError as e:
    print(f"Workflow not found: {e}")
except AuthenticationError:
    print("Invalid API key")
except ValidationError as e:
    print(f"Invalid input: {e}")
except RateLimitError:
    print("Rate limit exceeded")
except TimeoutError:
    print("Request timed out")
```

## API Reference

### Client

```python
AgentCanvasClient(
    api_key: str,
    base_url: str = "http://localhost:8000",
    timeout: float = 30.0
)
```

### Models

- `Workflow`: Workflow definition with nodes and edges
- `Execution`: Workflow execution with status and results
- `Provider`: LLM provider configuration
- `ExecutionStatus`: Enum (queued, running, succeeded, failed, cancelled)

### Input Models

- `CreateWorkflowInput`: Create new workflow
- `UpdateWorkflowInput`: Update existing workflow
- `StartExecutionInput`: Start workflow execution
- `CreateProviderInput`: Create new provider
- `UpdateProviderInput`: Update existing provider
- `PaginationParams`: Pagination parameters (skip, limit)

### Exceptions

- `AgentCanvasError`: Base exception
- `AuthenticationError`: Invalid API key (401)
- `NotFoundError`: Resource not found (404)
- `ValidationError`: Invalid input (422)
- `RateLimitError`: Rate limit exceeded (429)
- `TimeoutError`: Request timeout
- `ServerError`: Server error (5xx)

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy agentcanvas

# Code quality
ruff check agentcanvas
```

## Examples

See [examples/](examples/) for complete usage examples:

- `basic_usage.py`: Basic workflow and execution operations
- More examples coming soon

## License

MIT

## Contributing

Contributions welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

## Support

- 📖 [Documentation](https://docs.agentcanvas.dev)
- 💬 [GitHub Issues](https://github.com/yourusername/agentcanvas/issues)
- 🐛 [Bug Reports](https://github.com/yourusername/agentcanvas/issues/new?template=bug_report.md)
