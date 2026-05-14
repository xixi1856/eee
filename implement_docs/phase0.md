# 架构简化重写计划总览

## 目标
将现有"独立 Python Agent + Next.js 平台 + 凭证绑定"三体架构
简化为"Next.js 平台（含 TS Agent 核心）+ Python RAG 微服务"双服务架构。

## 核心决策
| 决策项 | 当前 | 目标 |
|---|---|---|
| Agent 进程 | 独立 Python 进程（edu-gateway）| TypeScript，嵌入 Next.js API Routes |
| 身份系统 | agent_user_id + 凭证绑定 + AgentIdentityMapping | 直接使用平台 JWT user_id |
| 渠道 | HTTP / WebSocket / CLI / WeChat / Feishu | 仅 HTTP（SSE） |
| 课程授权验证 | Agent 回调平台 /internal/course-rag-access | 平台在请求头注入 accessible_course_ids |
| RAG 存储 | 本地文件系统（vdb_*.json / .graphml）| PostgreSQL + pgvector（KV/向量）+ Neo4j（图），行级隔离 |
| Python 职责 | Agent 全部 + RAG | 仅 RAG 摄入与检索微服务 |

## 阶段概览

### Phase 1 — 架构简化（纯删减，无新增）
**目标**：删掉 bind/credential 系统和非 HTTP 渠道，统一身份，修改授权传递方式。
**工作量估计**：中（主要是删除代码 + 一次 DB migration）
**可独立验证**：是（现有 chat 流程全程可跑通）
**详见**：`phase1.md`

### Phase 2 — RAG 迁移到 PostgreSQL
**目标**：把 LightRAG 的文件存储切换到 PostgreSQL backend，个人 KB / 课程 KB 行级隔离。
**工作量估计**：中-高（LightRAG pg adapter 配置 + 现有数据迁移脚本）
**可独立验证**：是（RAG ingest + query 功能验证）
**详见**：`phase2.md`
**依赖**：Phase 1 完成（身份统一后 user_id 才是稳定主键）

### Phase 3 — TypeScript Agent 核心
**目标**：用 TypeScript 重新实现 ReAct 循环、工具系统、skills、subagent；
Python 仅保留 RAG 微服务（FastAPI，暴露 ingest / query / mindmap 端点）。
**工作量估计**：高
**可独立验证**：是（逐 tool 迁移，每迁移一个即可测试）
**依赖**：Phase 2 完成（TS Agent 直接读 PostgreSQL RAG 数据，无需再调旧 Python 工具层）
**详见**：`phase3.md`

## 执行顺序
Phase 1 → Phase 2 → Phase 3（严格串行，每阶段完成后验证通过再推进）

## 不变的部分（全程保留）
- Next.js App Router 页面结构
- Prisma 数据库 schema（除 Phase 1 删除的 3 张表外）
- JWT 认证体系（User / RefreshToken / CourseChatSession / QaLog）
- MinIO 材料存储
- Redis（RAG 任务队列）
- Skills markdown 驱动设计理念
- SubAgent 隔离执行理念