#!/bin/bash
# Post-create script for Dev Container

set -e

echo "🚀 Setting up AgentCanvas development environment..."

# Install backend dependencies
echo "📦 Installing backend dependencies..."
cd /workspace/backend
python -m uv sync

# Install frontend dependencies
echo "📦 Installing frontend dependencies..."
cd /workspace/frontend
pnpm install

# Copy .env if not exists
if [ ! -f /workspace/.env ]; then
    echo "📝 Creating .env file..."
    cp /workspace/.env.example /workspace/.env
    echo "⚠️  Please update .env with your API keys"
fi

# Run database migrations
echo "🗄️  Running database migrations..."
cd /workspace/backend
python -m uv run alembic upgrade head

echo "✅ Dev Container setup complete!"
echo ""
echo "Quick start:"
echo "  Backend:  cd backend && python -m uv run uvicorn app.main:app --reload --port 8000"
echo "  Frontend: cd frontend && pnpm dev"
echo ""
echo "Or use the one-click script:"
echo "  bash scripts/dev-start.sh"
