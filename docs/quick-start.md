# 开发环境快速开始

本指南帮助新开发者快速搭建 AgentCanvas 开发环境。

## 前置要求

- **Python 3.12+**（开发与 CI 实跑 3.13）
- **Node.js 20+**
- **pnpm**（`npm install -g pnpm`）
- **Git**

## 方式一：一键启动（推荐）

### Linux/macOS

```bash
# 克隆仓库
git clone https://github.com/your-org/agentcanvas.git
cd agentcanvas

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY 等必要密钥

# 一键启动
bash scripts/dev-start.sh
```

脚本会自动完成：
1. 检查依赖工具（Python、Node、pnpm、uv）
2. 安装后端依赖（uv sync）
3. 安装前端依赖（pnpm install）
4. 运行数据库迁移（alembic upgrade head）
5. 启动后端服务（http://127.0.0.1:8000）
6. 启动前端服务（http://127.0.0.1:5173）

### Windows

```cmd
# 克隆仓库
git clone https://github.com/your-org/agentcanvas.git
cd agentcanvas

# 配置环境变量
copy .env.example .env
REM 编辑 .env 填入 OPENAI_API_KEY 等必要密钥

# 一键启动
scripts\dev-start.bat
```

## 方式二：Dev Container（VS Code 推荐）

### 优势

- **零环境污染**：所有依赖在容器内
- **一致性**：团队成员环境完全一致
- **包含数据库**：PostgreSQL 17 + Redis 开箱即用

### 步骤

1. 安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
2. 安装 [VS Code Remote - Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
3. 打开项目：`code agentcanvas`
4. VS Code 提示 "Reopen in Container" → 点击确认
5. 容器构建完成后，终端执行：

```bash
# 后端
cd backend && python -m uv run uvicorn app.main:app --reload --port 8000

# 前端（新终端）
cd frontend && pnpm dev
```

**端口自动转发**：
- 后端 API: http://localhost:8000
- 前端: http://localhost:5173
- PostgreSQL: localhost:5432
- Redis: localhost:6379

## 方式三：手动启动

### 1. 安装依赖

```bash
# 安装 uv
pip install uv

# 后端依赖
cd backend
python -m uv venv
python -m uv sync

# 前端依赖
cd ../frontend
pnpm install
```

### 2. 配置环境

复制 `.env.example` 为 `.env` 并填入必要配置：

```bash
cp .env.example .env
```

生成 Fernet 密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

填入 `.env`：

```env
SECRET_KEY=your-generated-fernet-key
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
DATABASE_URL=sqlite+aiosqlite:///./data/agentcanvas.db
```

### 3. 数据库迁移

```bash
cd backend
python -m uv run alembic upgrade head

# 可选：灌入演示数据
python -m uv run python -m app.services
```

### 4. 启动服务

**后端**（终端 1）：

```bash
cd backend
python -m uv run uvicorn app.main:app --reload --port 8000
```

**前端**（终端 2）：

```bash
cd frontend
pnpm dev
```

### 5. 验证

- 前端: http://127.0.0.1:5173
- 后端 API 文档: http://127.0.0.1:8000/docs
- 健康检查: http://127.0.0.1:8000/readyz

## VS Code 调试配置

项目已配置 8 个调试配置 + 2 个组合：

### 单独调试

- **Backend: FastAPI** - 调试后端 API
- **Backend: Worker** - 调试执行队列 Worker
- **Backend: Tests** - 调试测试套件
- **Backend: Single Test** - 调试当前测试文件
- **Frontend: Dev Server** - 调试前端
- **Frontend: E2E Tests** - 调试 Playwright E2E
- **Python SDK: Tests** - 调试 Python SDK
- **CLI: Run Command** - 调试 CLI 工具

### 组合调试

- **Full Stack** - 同时启动前后端（F5 一键启动）
- **Backend + Worker** - 同时启动 API 和 Worker

**使用方法**：
1. 打开 VS Code
2. 按 `F5` 或点击左侧 Debug 图标
3. 选择配置并启动

## 数据库管理工具

推荐使用以下 GUI 工具查看数据库：

### SQLite（默认）

- **[DBeaver](https://dbeaver.io/)** - 跨平台，免费开源
- **[TablePlus](https://tableplus.com/)** - Mac/Windows，免费版可用
- **[DB Browser for SQLite](https://sqlitebrowser.org/)** - 专用 SQLite 工具

数据库文件位置：`backend/data/agentcanvas.db`

### PostgreSQL（生产）

- **[DBeaver](https://dbeaver.io/)**
- **[TablePlus](https://tableplus.com/)**
- **[pgAdmin](https://www.pgadmin.org/)** - PostgreSQL 官方工具

连接配置：
- Host: localhost
- Port: 5432
- Database: agentcanvas
- User/Password: 见 `.env`

详细查询示例见 [docs/database-tools.md](../database-tools.md)

## 常见问题

### uv 未安装

```bash
pip install uv
```

### pnpm 未安装

```bash
npm install -g pnpm
```

### 后端启动失败：端口占用

```bash
# 查找占用 8000 端口的进程
# Linux/Mac
lsof -i :8000

# Windows
netstat -ano | findstr :8000

# 杀死进程或更换端口
uvicorn app.main:app --reload --port 8001
```

### 前端启动失败：依赖问题

```bash
cd frontend
rm -rf node_modules pnpm-lock.yaml
pnpm install
```

### 数据库迁移失败

```bash
# 重置数据库
cd backend
rm -rf data/
python -m uv run alembic upgrade head
```

### Dev Container 构建慢

首次构建需要下载 Docker 镜像和安装依赖，约 5-10 分钟。后续重新打开会直接复用。

## 下一步

- 阅读 [DESIGN.md](../DESIGN.md) 了解设计系统
- 查看 [docs/plan.md](plan.md) 了解架构设计
- 运行测试：`cd backend && python -m uv run pytest -v`
- 尝试创建第一个工作流

## 获取帮助

- 查看文档：[docs/](.)
- 提交问题：[GitHub Issues](https://github.com/your-org/agentcanvas/issues)
- 贡献代码：参考 [CONTRIBUTING.md](../CONTRIBUTING.md)（如有）
