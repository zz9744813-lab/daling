# 当前系统架构状态（Phase 0 基线）

> 记录时间：2026-07-10
> 分支：rescue/persistent-pipeline
> Tag：before-pipeline-rescue

## 系统概览

Novel Agent OS 是一个多 Agent 长篇小说自动写作系统。

### 技术栈
- 后端：Python 3.11 + FastAPI + SQLAlchemy Async + PostgreSQL/SQLite
- 前端：React + TypeScript + Vite + TanStack Query + Tailwind CSS
- LLM：OpenAI Compatible Provider + Anthropic Provider

### 核心模块
- `backend/app/agents/` — 8 个 Agent（StoryArchitect, ChapterPlanner, Drafter, Critic, ContinuityGuard, Rewriter, ChiefEditor, MemoryKeeper）
- `backend/app/pipeline/orchestrator.py` — Pipeline 编排器
- `backend/app/model_gateway/` — LLM 网关
- `backend/app/services/continuous_production.py` — 内存型连续写作（待重构）
- `frontend/src/pages/` — React 页面

## 已知核心问题

### 1. 失败伪装成成功（致命）
所有 Agent 在 LLM 调用失败时返回默认值而非抛出异常：
- ChapterPlanner: 返回空 `{}`
- Drafter: 返回占位文本 `"（正文生成失败，请稍后重试）"`
- Critic: 默认所有维度 75 分
- ContinuityGuard: 默认 `passed=True`
- Rewriter: 返回空列表 `[]`

### 2. Rewriter 清空正文风险（致命）
Orchestrator 先调用 `_delete_blocks()` 删除旧 Block，再添加 Rewriter 返回的新 Block。
当 Rewriter 返回 `[]` 时，章节正文被彻底清空。

### 3. 未批准章节执行记忆更新（严重）
MemoryKeeper 在检查 `approved` 之前被调用，未批准内容污染全局故事状态。

### 4. 连续写作不持久（高）
`ContinuousProductionService` 使用 `asyncio.create_task` + 进程内存字典，重启后丢失。

### 5. 核心 Agent 走非流式（中）
虽然 Provider 定义了 `stream_complete()`，但 Agent 调用 `gateway.complete()`。

### 6. 无测试（高）
仓库中无任何测试文件。

## 基线测试结果
- pytest: 0 tests collected
- ruff: ~12 个 import 排序问题（I001）
- frontend typecheck: 通过
- frontend build: 通过
