# 执行历史导出功能

## 功能概述

为工作流执行历史添加了 CSV 导出功能，允许用户将所有执行记录导出为 CSV 文件用于分析和审计。

## 实现细节

### 新增文件

1. **`frontend/src/features/execution/ExportExecutions.tsx`**
   - 独立的导出组件
   - 使用游标分页（cursor-based pagination）获取所有执行记录
   - 生成包含 BOM 的 CSV 文件以支持 Excel UTF-8 编码
   - CSV 字段：执行ID、状态、触发来源、版本号、开始时间、结束时间、持续时间、错误信息
   - 文件名格式：`{工作流名称}-executions-{时间戳}.csv`

### 修改文件

1. **`frontend/src/features/execution/ExecutionHistory.tsx`**
   - 添加 `workflowName` 可选属性到 Props 接口
   - 在历史记录弹窗头部集成 ExportExecutions 组件
   - 导出按钮位于刷新按钮和关闭按钮之间

2. **`frontend/src/features/canvas/CanvasCommandBar.tsx`**
   - 将 `props.name` (工作流名称) 传递给 ExecutionHistory 组件

## 技术特性

### 分页处理
- 使用游标分页自动获取所有记录（每次 100 条）
- 循环直到 `has_more` 为 false 或 `next_cursor` 为 null

### CSV 格式
- 所有字段用双引号包裹
- 错误信息中的双引号被转义为 `""`
- 添加 UTF-8 BOM (`﻿`) 确保 Excel 正确识别编码

### 用户体验
- 导出按钮显示加载状态（"导出中..."）
- 空记录时显示提示信息
- 导出失败时显示错误提示
- 使用 `download` 属性触发浏览器下载

## 验证

1. ✅ TypeScript 类型检查通过
2. ⏳ 端到端测试运行中

## 使用方式

1. 在画布页面打开工作流
2. 点击右上角"历史"按钮
3. 在弹出的执行历史面板中点击"导出CSV"按钮
4. CSV 文件将自动下载到本地

## 后续优化建议

- 添加导出进度条（当记录数量巨大时）
- 支持更多导出格式（JSON, Excel）
- 添加筛选条件导出（按状态、时间范围等）
- 导出到后端 API 生成文件（避免前端内存限制）
