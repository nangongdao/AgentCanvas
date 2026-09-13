@echo off
REM AgentCanvas 一键启动开发环境脚本 (Windows)

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."

echo.
echo ================================
echo AgentCanvas 开发环境启动脚本
echo ================================
echo.

REM 检查 Python
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] Python 未安装，请先安装 Python 3.12+
    exit /b 1
)
echo [OK] Python 已安装

REM 检查 Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo [错误] Node.js 未安装，请先安装 Node.js 20+
    exit /b 1
)
echo [OK] Node.js 已安装

REM 检查 pnpm
where pnpm >nul 2>&1
if errorlevel 1 (
    echo [错误] pnpm 未安装，请运行: npm install -g pnpm
    exit /b 1
)
echo [OK] pnpm 已安装

REM 检查 uv
python -m uv --version >nul 2>&1
if errorlevel 1 (
    echo [提示] uv 未安装，正在安装...
    pip install uv
)
echo [OK] uv 已安装

REM 检查 .env 文件
if not exist "%PROJECT_ROOT%\.env" (
    echo [警告] .env 文件不存在，从 .env.example 复制...
    copy "%PROJECT_ROOT%\.env.example" "%PROJECT_ROOT%\.env"
    echo [错误] 请编辑 .env 文件，填入必要的 API 密钥
    exit /b 1
)
echo [OK] .env 文件存在

REM 后端依赖安装
echo.
echo 安装后端依赖...
cd /d "%PROJECT_ROOT%\backend"
if not exist ".venv" (
    echo 创建虚拟环境...
    python -m uv venv
)
python -m uv sync
echo [OK] 后端依赖已安装

REM 前端依赖安装
echo.
echo 安装前端依赖...
cd /d "%PROJECT_ROOT%\frontend"
if not exist "node_modules" (
    pnpm install
) else (
    echo node_modules 已存在，跳过安装
)
echo [OK] 前端依赖已安装

REM 数据库迁移
echo.
echo 运行数据库迁移...
cd /d "%PROJECT_ROOT%\backend"
python -m uv run alembic upgrade head
echo [OK] 数据库迁移完成

REM 启动服务
echo.
echo ================================
echo 准备完成，启动服务...
echo ================================
echo.

REM 启动后端（新窗口）
echo 启动后端服务 (http://127.0.0.1:8000)...
start "AgentCanvas Backend" cmd /k "cd /d %PROJECT_ROOT%\backend && python -m uv run uvicorn app.main:app --reload --port 8000"

REM 等待后端就绪
echo 等待后端启动...
set /a count=0
:wait_backend
timeout /t 1 /nobreak >nul
curl -s http://127.0.0.1:8000/readyz >nul 2>&1
if errorlevel 1 (
    set /a count+=1
    if !count! geq 30 (
        echo [错误] 后端启动超时
        exit /b 1
    )
    goto wait_backend
)
echo [OK] 后端服务已就绪

REM 启动前端（新窗口）
echo.
echo 启动前端服务 (http://127.0.0.1:5173)...
start "AgentCanvas Frontend" cmd /k "cd /d %PROJECT_ROOT%\frontend && pnpm dev"

REM 显示启动信息
echo.
echo ================================
echo 开发环境已启动
echo ================================
echo 后端: http://127.0.0.1:8000
echo 前端: http://127.0.0.1:5173
echo API 文档: http://127.0.0.1:8000/docs
echo 就绪探针: http://127.0.0.1:8000/readyz
echo.
echo 关闭命令行窗口以停止服务
echo ================================
echo.

pause
