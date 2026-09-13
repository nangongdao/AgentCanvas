#!/bin/bash
# AgentCanvas 一键启动开发环境脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🚀 AgentCanvas 开发环境启动脚本"
echo "================================"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# 检查必要工具
check_tool() {
    if ! command -v "$1" &> /dev/null; then
        echo -e "${RED}✗ $1 未安装${NC}"
        echo "  请先安装: $2"
        return 1
    else
        echo -e "${GREEN}✓ $1 已安装${NC}"
        return 0
    fi
}

echo ""
echo "检查依赖工具..."
check_tool "python" "Python 3.12+" || exit 1
check_tool "node" "Node.js 20+" || exit 1
check_tool "pnpm" "pnpm (npm install -g pnpm)" || exit 1

# 检查 uv
if ! python -m uv --version &> /dev/null; then
    echo -e "${YELLOW}⚠ uv 未安装，正在安装...${NC}"
    pip install uv
fi
echo -e "${GREEN}✓ uv 已安装${NC}"

# 检查 .env 文件
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${YELLOW}⚠ .env 文件不存在，从 .env.example 复制...${NC}"
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    echo -e "${RED}⚠ 请编辑 .env 文件，填入必要的 API 密钥${NC}"
    exit 1
fi
echo -e "${GREEN}✓ .env 文件存在${NC}"

# 后端依赖安装
echo ""
echo "安装后端依赖..."
cd "$PROJECT_ROOT/backend"
if [ ! -d ".venv" ]; then
    echo "创建虚拟环境..."
    python -m uv venv
fi
python -m uv sync
echo -e "${GREEN}✓ 后端依赖已安装${NC}"

# 前端依赖安装
echo ""
echo "安装前端依赖..."
cd "$PROJECT_ROOT/frontend"
if [ ! -d "node_modules" ]; then
    pnpm install
else
    echo "node_modules 已存在，跳过安装"
fi
echo -e "${GREEN}✓ 前端依赖已安装${NC}"

# 数据库迁移
echo ""
echo "运行数据库迁移..."
cd "$PROJECT_ROOT/backend"
python -m uv run alembic upgrade head
echo -e "${GREEN}✓ 数据库迁移完成${NC}"

# 启动服务
echo ""
echo "================================"
echo "🎉 准备完成，启动服务..."
echo "================================"

# 使用 trap 捕获 Ctrl+C
trap 'kill $(jobs -p) 2>/dev/null' EXIT

# 启动后端
echo ""
echo "启动后端服务 (http://127.0.0.1:8000)..."
cd "$PROJECT_ROOT/backend"
python -m uv run uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

# 等待后端就绪
echo "等待后端启动..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:8000/readyz > /dev/null 2>&1; then
        echo -e "${GREEN}✓ 后端服务已就绪${NC}"
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        echo -e "${RED}✗ 后端启动超时${NC}"
        exit 1
    fi
done

# 启动前端
echo ""
echo "启动前端服务 (http://127.0.0.1:5173)..."
cd "$PROJECT_ROOT/frontend"
pnpm dev &
FRONTEND_PID=$!

# 等待用户中断
echo ""
echo "================================"
echo -e "${GREEN}✨ 开发环境已启动${NC}"
echo "================================"
echo "后端: http://127.0.0.1:8000"
echo "前端: http://127.0.0.1:5173"
echo "API 文档: http://127.0.0.1:8000/docs"
echo "就绪探针: http://127.0.0.1:8000/readyz"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo "================================"

# 等待任一进程退出
wait -n
