# AgentCanvas CLI

Command-line interface for managing AgentCanvas installations.

## Installation

### From PyPI (when published)

```bash
pip install agentcanvas-cli
```

### From Source

```bash
cd cli
pip install -e .
```

## Usage

### Initialize a New Project

Create `.env` configuration in the current directory:

```bash
agentcanvas init
```

Options:
- `--dir PATH`: Target directory (default: current directory)
- `--db [sqlite|postgresql]`: Database backend (default: sqlite)

Example with PostgreSQL:

```bash
agentcanvas init --db postgresql
```

### Start Development Server

Launch both frontend and backend in development mode:

```bash
agentcanvas dev
```

Options:
- `--host HOST`: Host to bind (default: 0.0.0.0)
- `--backend-port PORT`: Backend port (default: 8000)
- `--frontend-port PORT`: Frontend port (default: 5173)
- `--no-frontend`: Start backend only

Example:

```bash
# Custom ports
agentcanvas dev --backend-port 8080 --frontend-port 3000

# Backend only
agentcanvas dev --no-frontend
```

### Run Database Migrations

Apply pending Alembic migrations:

```bash
agentcanvas migrate
```

Options:
- `--revision REV`: Target revision (default: head)

Example:

```bash
# Migrate to specific revision
agentcanvas migrate --revision abc123
```

### Backup Installation

Create a complete backup of your AgentCanvas installation:

```bash
agentcanvas backup
```

Options:
- `--output PATH`: Backup file path (default: agentcanvas_backup.tar.gz)
- `--include-data/--no-include-data`: Include database data (default: yes)

Example:

```bash
agentcanvas backup --output ~/backups/agentcanvas_20260913.tar.gz
```

## Quick Start Workflow

```bash
# 1. Initialize project
cd my-agentcanvas
agentcanvas init

# 2. Edit .env to add API keys
nano .env

# 3. Run migrations
agentcanvas migrate

# 4. Start development server
agentcanvas dev
```

## Configuration

The `agentcanvas init` command generates a `.env` file with:

- **Database URL**: SQLite or PostgreSQL connection string
- **Secret Key**: Auto-generated secure secret for sessions
- **Server Config**: Host, port, CORS settings
- **LLM Provider Keys**: Placeholders for Anthropic, OpenAI, Gemini, etc.
- **MCP Tokens**: GitHub, Slack, Notion integration tokens

## Requirements

- Python 3.11+
- Node.js 18+ (for frontend development)
- pnpm 9+ (for frontend package management)
- uv (for Python dependency management)

## Development

```bash
# Install in editable mode with dev dependencies
cd cli
pip install -e ".[dev]"

# Run type checks
mypy agentcanvas_cli/

# Run linter
ruff check agentcanvas_cli/
```

## License

MIT
