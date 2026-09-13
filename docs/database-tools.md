# 数据库管理工具配置

AgentCanvas 支持多种数据库 GUI 工具进行开发调试。

## 推荐工具

### 1. DBeaver (跨平台，免费开源)

**下载**: https://dbeaver.io/download/

**SQLite 连接配置**:
- Host: localhost
- Database: `backend/data/agentcanvas.db`
- Driver: SQLite JDBC

**PostgreSQL 连接配置**:
- Host: localhost
- Port: 5432
- Database: agentcanvas
- Username: agentcanvas
- Password: (见 `.env` 文件)

### 2. TablePlus (Mac/Windows，免费版可用)

**下载**: https://tableplus.com/

**SQLite 连接**:
1. Create new connection → SQLite
2. Database path: `backend/data/agentcanvas.db`

**PostgreSQL 连接**:
1. Create new connection → PostgreSQL
2. Host: localhost:5432
3. User/Password: 见 `.env`

### 3. DataGrip (JetBrains，付费)

**下载**: https://www.jetbrains.com/datagrip/

配置方式同 DBeaver。

### 4. pgAdmin (PostgreSQL 专用)

**下载**: https://www.pgadmin.org/download/

仅支持 PostgreSQL，生产部署推荐。

## 常用查询

### 查看工作流列表
```sql
SELECT id, name, version, created_at 
FROM workflows 
ORDER BY created_at DESC 
LIMIT 10;
```

### 查看执行历史
```sql
SELECT 
    e.id,
    e.status,
    w.name as workflow_name,
    e.started_at,
    e.duration
FROM executions e
JOIN workflows w ON e.workflow_id = w.id
ORDER BY e.started_at DESC
LIMIT 20;
```

### 查看执行事件流
```sql
SELECT 
    seq,
    event_type,
    node_id,
    timestamp
FROM execution_events
WHERE execution_id = 'your-execution-id'
ORDER BY seq;
```

### 查看 Provider 配置
```sql
SELECT id, name, type, models, created_at
FROM providers
ORDER BY created_at DESC;
```

### 查看 MCP 连接
```sql
SELECT id, name, transport_type, status, created_at
FROM mcp_connections
ORDER BY created_at DESC;
```

### 查看用户配额
```sql
SELECT 
    u.username,
    q.max_workflows,
    q.max_executions_per_day,
    q.max_execution_duration_seconds
FROM users u
LEFT JOIN quotas q ON u.quota_id = q.id;
```

## 开发技巧

### 重置数据库
```bash
cd backend
rm data/agentcanvas.db
python -m uv run alembic upgrade head
python -m uv run python -m app.services  # 灌入演示数据
```

### 查看迁移历史
```bash
cd backend
python -m uv run alembic history
python -m uv run alembic current
```

### 数据库备份
```bash
# SQLite
cp backend/data/agentcanvas.db backend/data/agentcanvas.db.backup

# PostgreSQL
pg_dump -U agentcanvas -h localhost agentcanvas > backup.sql
```

### 数据库恢复
```bash
# SQLite
cp backend/data/agentcanvas.db.backup backend/data/agentcanvas.db

# PostgreSQL
psql -U agentcanvas -h localhost agentcanvas < backup.sql
```

## Redis 管理

### Redis Commander (Web UI)
```bash
npm install -g redis-commander
redis-commander --redis-host localhost --redis-port 6379
```

访问 http://localhost:8081

### RedisInsight (官方 GUI)

**下载**: https://redis.io/insight/

连接配置:
- Host: localhost
- Port: 6379
- Name: AgentCanvas Dev

### 常用 Redis 命令
```bash
# 连接 Redis
redis-cli

# 查看所有 keys
KEYS *

# 查看事件流
XINFO STREAM execution_events

# 查看队列长度
LLEN execution_queue

# 清空数据库 (谨慎!)
FLUSHDB
```
