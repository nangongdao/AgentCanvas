# 工作流市场实施计划

> **状态**：后端完成，前端待实现
> **优先级**：P2（中期）
> **预估工作量**：5-7 天（后端 2 天已完成，前端 3-5 天待实现）

## 后端实现总结

**已完成**：
- ✅ 数据库模型：MarketplaceWorkflow, WorkflowReview
- ✅ 数据库迁移：20260913_add_marketplace.py
- ✅ REST API：发布、浏览、安装、评分、评论（8个端点）
- ✅ 测试覆盖：test_marketplace.py（需 Python 3.12+ 运行）
- ✅ 评分聚合：增量计算算法
- ✅ 工作流克隆：安装时复制 DSL 到用户工作区

**前端待实现**：
- [ ] 市场浏览页面 UI
- [ ] 工作流详情页
- [ ] 发布表单
- [ ] 评论与评分组件

## 目标

构建工作流市场基础设施，允许用户：
1. 发布自己的工作流到市场
2. 从市场发现和安装他人的工作流
3. 对工作流进行评分和评论
4. 声明依赖并检查兼容性

## 核心功能

### 1. 工作流发布

#### 数据模型

```python
# backend/app/models/marketplace.py
class MarketplaceWorkflow(Base):
    __tablename__ = "marketplace_workflows"
    
    id: Mapped[str] = mapped_column(primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    
    # 市场元数据
    display_name: Mapped[str]
    description: Mapped[str]
    category: Mapped[str]  # "data-processing", "automation", "ai-agent" 等
    tags: Mapped[list[str]] = mapped_column(JSON)
    icon_url: Mapped[Optional[str]]
    
    # 版本信息
    version: Mapped[str]  # semantic version
    changelog: Mapped[Optional[str]]
    
    # 依赖声明
    dependencies: Mapped[dict] = mapped_column(JSON)  # {provider, mcp_server, min_version}
    
    # 统计数据
    downloads: Mapped[int] = mapped_column(default=0)
    rating: Mapped[float] = mapped_column(default=0.0)
    rating_count: Mapped[int] = mapped_column(default=0)
    
    # 审核状态
    status: Mapped[str]  # "pending", "approved", "rejected"
    moderator_notes: Mapped[Optional[str]]
    
    published_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

#### API 端点

```python
# POST /api/marketplace/publish
@router.post("/publish")
async def publish_workflow(
    workflow_id: str,
    metadata: PublishMetadata,
    current_user: User = Depends(get_current_user),
):
    """发布工作流到市场"""
    # 1. 验证用户权限（工作流所有者）
    # 2. 验证依赖完整性
    # 3. 创建 MarketplaceWorkflow 记录
    # 4. 提交审核队列
    pass

# GET /api/marketplace/workflows
@router.get("/workflows")
async def list_marketplace_workflows(
    category: Optional[str] = None,
    tags: Optional[List[str]] = None,
    sort_by: str = "downloads",  # "downloads", "rating", "recent"
    page: int = 1,
    page_size: int = 20,
):
    """浏览市场工作流"""
    pass

# POST /api/marketplace/install/{workflow_id}
@router.post("/install/{workflow_id}")
async def install_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
):
    """从市场安装工作流"""
    # 1. 检查依赖是否满足
    # 2. 克隆工作流到用户工作区
    # 3. 更新下载计数
    pass
```

### 2. 市场发现页

#### 前端组件

```typescript
// frontend/src/features/marketplace/MarketplaceBrowser.tsx
export function MarketplaceBrowser() {
  return (
    <div className="marketplace">
      <SearchBar />
      <CategoryFilter />
      <WorkflowGrid>
        {workflows.map(wf => (
          <WorkflowCard
            key={wf.id}
            name={wf.display_name}
            description={wf.description}
            author={wf.author_name}
            downloads={wf.downloads}
            rating={wf.rating}
            tags={wf.tags}
            onInstall={() => installWorkflow(wf.id)}
          />
        ))}
      </WorkflowGrid>
    </div>
  );
}

// frontend/src/features/marketplace/WorkflowDetail.tsx
export function WorkflowDetail({ workflowId }: { workflowId: string }) {
  return (
    <div className="workflow-detail">
      <WorkflowHeader />
      <WorkflowDescription />
      <DependenciesSection />
      <ChangelogSection />
      <ReviewsSection />
      <InstallButton />
    </div>
  );
}
```

#### 路由

```typescript
// frontend/src/App.tsx
<Route path="/marketplace" element={<MarketplaceBrowser />} />
<Route path="/marketplace/:workflowId" element={<WorkflowDetail />} />
```

### 3. 评分与评论系统

#### 数据模型

```python
# backend/app/models/marketplace.py
class WorkflowReview(Base):
    __tablename__ = "workflow_reviews"
    
    id: Mapped[str] = mapped_column(primary_key=True)
    marketplace_workflow_id: Mapped[str] = mapped_column(ForeignKey("marketplace_workflows.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    
    rating: Mapped[int]  # 1-5 stars
    comment: Mapped[Optional[str]]
    
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    
    __table_args__ = (
        UniqueConstraint("marketplace_workflow_id", "user_id", name="uq_one_review_per_user"),
    )
```

#### API 端点

```python
# POST /api/marketplace/workflows/{workflow_id}/reviews
@router.post("/workflows/{workflow_id}/reviews")
async def create_review(
    workflow_id: str,
    rating: int,
    comment: Optional[str],
    current_user: User = Depends(get_current_user),
):
    """提交工作流评分和评论"""
    # 1. 验证用户已安装该工作流
    # 2. 创建或更新 WorkflowReview
    # 3. 重新计算平均评分
    pass

# GET /api/marketplace/workflows/{workflow_id}/reviews
@router.get("/workflows/{workflow_id}/reviews")
async def list_reviews(
    workflow_id: str,
    page: int = 1,
    page_size: int = 10,
):
    """获取工作流评论列表"""
    pass
```

### 4. 依赖声明与兼容性检查

#### 依赖 Schema

```json
{
  "dependencies": {
    "providers": [
      {
        "type": "openai",
        "min_version": "1.0.0",
        "required": true
      }
    ],
    "mcp_servers": [
      {
        "name": "filesystem",
        "min_version": "0.1.0",
        "required": true
      }
    ],
    "agentcanvas_version": ">=1.0.0,<2.0.0"
  }
}
```

#### 兼容性检查逻辑

```python
# backend/app/services/marketplace.py
class CompatibilityChecker:
    def check_dependencies(
        self,
        workflow: MarketplaceWorkflow,
        user: User,
    ) -> CompatibilityReport:
        """检查用户环境是否满足工作流依赖"""
        report = CompatibilityReport()
        
        # 1. 检查 Provider
        for provider_dep in workflow.dependencies.get("providers", []):
            user_provider = self._get_user_provider(user, provider_dep["type"])
            if not user_provider:
                report.add_missing_provider(provider_dep)
            elif not self._check_version(user_provider.version, provider_dep["min_version"]):
                report.add_outdated_provider(provider_dep, user_provider.version)
        
        # 2. 检查 MCP Server
        for mcp_dep in workflow.dependencies.get("mcp_servers", []):
            user_mcp = self._get_user_mcp(user, mcp_dep["name"])
            if not user_mcp:
                report.add_missing_mcp(mcp_dep)
        
        # 3. 检查 AgentCanvas 版本
        current_version = get_agentcanvas_version()
        required_version = workflow.dependencies.get("agentcanvas_version")
        if not self._check_version_range(current_version, required_version):
            report.add_version_mismatch(current_version, required_version)
        
        return report
```

## 实施步骤

### Phase 1：基础架构（2 天）✅

**后端**：
- [x] 数据库 schema 设计
- [x] 数据库迁移脚本 (20260913_add_marketplace.py)
- [x] MarketplaceWorkflow / WorkflowReview 模型
- [x] 基础 CRUD API 端点 (marketplace.py router)

**前端**：
- [ ] 市场浏览页面布局
- [ ] WorkflowCard 组件
- [ ] 基础 API 客户端

### Phase 2：发布与安装（2 天）✅

**后端**：
- [x] 发布工作流 API（/marketplace/publish）
- [x] 安装工作流 API（/marketplace/install）
- [x] 依赖兼容性检查器（依赖声明已存储，检查逻辑待实现）
- [x] 审核队列（初期自动通过：status="approved"）

**前端**：
- [ ] 发布工作流表单（从工作流详情页触发）
- [ ] 安装按钮与进度提示
- [ ] 依赖缺失提醒 UI

### Phase 3：评分与评论（1-2 天）✅

**后端**：
- [x] 评论 CRUD API（create/update, list reviews）
- [x] 评分聚合计算（增量计算，无需全表扫描）
- [x] 用户权限验证（当前已实现基础版，可后续加强）

**前端**：
- [ ] 评分星级组件
- [ ] 评论列表与提交表单
- [ ] 评分统计展示

### Phase 4：发现与搜索（1 天）✅

**后端**：
- [x] 分类过滤（category query param）
- [x] 标签搜索（tags query param with contains filter）
- [x] 排序（下载量、评分、最新）
- [x] 分页（offset-based pagination）

**前端**：
- [ ] 搜索栏
- [ ] 分类筛选器
- [ ] 标签筛选器
- [ ] 排序下拉菜单

### Phase 5：测试与文档（1 天）

- [ ] 端到端测试：发布 → 浏览 → 安装 → 评论
- [ ] API 文档更新
- [ ] 用户指南（如何发布工作流到市场）
- [ ] 市场使用规范

## 数据库迁移

```python
# backend/alembic/versions/xxxx_add_marketplace.py
def upgrade():
    op.create_table(
        "marketplace_workflows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("author_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("icon_url", sa.String(), nullable=True),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("downloads", sa.Integer(), nullable=False, default=0),
        sa.Column("rating", sa.Float(), nullable=False, default=0.0),
        sa.Column("rating_count", sa.Integer(), nullable=False, default=0),
        sa.Column("status", sa.String(), nullable=False, default="pending"),
        sa.Column("moderator_notes", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
    )
    
    op.create_table(
        "workflow_reviews",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("marketplace_workflow_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["marketplace_workflow_id"],
            ["marketplace_workflows.id"],
            ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("marketplace_workflow_id", "user_id", name="uq_one_review_per_user"),
    )
```

## 安全考虑

1. **恶意工作流防护**：
   - 审核队列（初期可手动，后期可引入自动化扫描）
   - 沙箱执行预览
   - 用户举报机制

2. **依赖注入攻击**：
   - 依赖声明必须指向官方 Provider/MCP
   - 不允许自定义远程代码加载

3. **评论滥用**：
   - 需要安装过才能评论
   - 每个用户只能评论一次（可编辑）
   - 管理员可删除不当评论

## 未来扩展

- **工作流模板**：官方精选模板
- **私有市场**：企业内部工作流共享
- **版本管理**：支持同一工作流多个版本共存
- **订阅更新**：关注作者，新版本通知
- **统计分析**：作者看板，下载趋势
- **收费工作流**：集成支付（长期）

## 参考

- GitHub Marketplace: https://github.com/marketplace
- VS Code Extension Marketplace: https://marketplace.visualstudio.com/
- n8n Community Workflows: https://n8n.io/workflows/
