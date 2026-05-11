# EduAgent 分阶段实现路线图（Phase 0 总览）

> 本文档仅做任务清单和验收标准概述，各阶段详细方案在对应 `phaseN.md` 中展开。

---

## 总体思路

整个项目分为两条主线，顺序推进：

**主线 A：Agent 核心完善（A1–A5）**
先把 Python Agent 建设为一个完整、可独立运行、可多 channel 接入的服务，再接教育平台。

**主线 B：教育平台（B1–B3）**
**Next.js（App Router，全栈）** 作为教育平台主应用：页面与路由、**Route Handlers / Server Actions** 承载 REST 与流式代理、服务端直连 PostgreSQL / Redis / MinIO。UI 层可选用 **Ant Design** 等组件库与现有设计习惯对齐。教育平台视为 Agent 的众多 channel 之一，通过凭证码绑定用户身份，通过事件上报收集学习数据。

---

## 主线 A：Agent 核心完善

### A1 — 配置与 Provider 运行时

**核心任务**
- 建立独立的 `edu_agent.config` 模块（EduSettings、ProviderConfig），脱离 `rag_mvp.config`。
- 建立 `edu_agent.paths`，统一管理 EDU_HOME、workspace、session、memory 路径。
- 建立 `edu_agent.providers` 层：provider 注册表、运行时解析（model/api_key/base_url）、重试/退避/fallback。
- 支持从 `edu_agent.yaml` 或 `.env` 加载配置，支持多 provider 切换。

**验收**
- 可通过配置文件切换 OpenAI / DeepSeek / Ollama 等 provider，无需改代码。
- 所有路径通过 `edu_agent.paths` 统一访问。
- 网络抖动时自动重试并记录日志。

---

### A2 — 上下文管理与会话存储

**核心任务**
- 建立 `edu_agent.context` 模块：token 预算估算、历史裁剪、摘要压缩。
- 建立 `edu_agent.sessions.db`（SQLite）：session 生命周期、消息持久化、tool call 记录、session index。
- 替换当前 append-only JSONL，支持 session 创建、恢复、归档。
- 添加简单 session 搜索能力（关键词）。

**验收**
- 单 session 消息超过 token 预算时，自动压缩旧历史，不截断工具结果和用户意图。
- 重启 CLI 后可以通过 session_id 恢复上次对话。
- SQLite 中能查到每一轮的 tool call 记录和耗时。

---

### A3 — 长期记忆与学习者画像自动更新

**核心任务**
- 建立 `edu_agent.memory` 模块：统一记忆管理、记忆提供者接口（初期本地 JSON/Markdown）。
- 实现 memory consolidator：会话结束或达到阈值后，自动提取事实、偏好、知识薄弱点，更新学习者画像。
- 提供 `remember_fact`、`search_memory`、`update_profile` 工具，让 Agent 可主动操作记忆。

**验收**
- 会话结束后，学习者画像自动新增本次会话的关键事实（无需用户手动触发）。
- Agent 可以在下一次会话中主动引用上次记忆的内容。
- 记忆检索可按关键词找到历史事实。

---

### A4 — 工具运行时、Toolset 权限与 MCP 集成

**核心任务**
- 完善 `edu_agent.toolsets`：定义核心工具集（rag、web、files、memory、delegation、scheduling、mcp），支持按场景启用不同工具集。
- 建立工具运行时层：参数校验、结果截断、耗时统计、错误分类。
- 建立工具权限层：read / write / network / execute 分级，对写操作和网络操作加 CLI 审批提示。
- 建立 `edu_agent.mcp` 模块：stdio/HTTP MCP client，将外部 MCP tools 动态注册到 ToolRegistry。
- 改造 `knowledge_query` 工具，支持多来源 RAG 查询：
  - **个人 RAG**：用户自己上传的资料，本地 JSON/JSONL（当前 `rag_storage/`），仅本人 Agent 可索引。
  - **课程 RAG**：教师上传的课程资料，存储于 PostgreSQL（pgvector + LightRAG namespace 隔离），所有加入该课程的学生 Agent 共享索引，课程间知识图谱完全独立不共享。
  - session context 中存在 `course_id` 时，优先查课程 RAG，再补充查个人 RAG；无 `course_id` 时仅查个人 RAG。
  - 返回结果携带 `origin`（course / personal）和来源文档信息，供前端展示。

**验收**
- 可以通过配置文件启用/禁用某个 toolset，不影响其他工具。
- 工具调用时间超过阈值自动记录 warn 日志。
- 可以连接一个标准 MCP server，Agent 能调用其中的工具。
- 写文件/调度等危险操作在 CLI 模式下需用户确认。
- `knowledge_query` 在有课程上下文的 session 中能同时返回课程 RAG 和个人 RAG 的结果，且来源标记正确。

---

### A5 — 消息总线、SessionRunner 与 Gateway

**核心任务**
- 建立 `edu_agent.bus`：InboundMessage / OutboundMessage 统一消息模型。
- 建立 `edu_agent.runner`：按 session_id 串行处理，跨 session 并发，负责 Agent 创建/恢复。
- 建立 `edu_agent.gateway`：长运行进程，管理 channel 注册、session 路由、授权、消息分发。
- 实现第一个 channel adapter：`edu_agent.channels.websocket`（或 HTTP SSE），供后续平台接入。
- 建立 `edu_agent.api.server`：FastAPI HTTP API，`/v1/chat/completions`（支持 SSE streaming）、`/sessions`、`/tools`。

**验收**
- 通过 HTTP API 可以创建 session、发送消息、接收流式回复。
- WebSocket channel 可以同时处理多个 session，session 内消息串行。
- CLI 依然可用，CLI 作为一个 channel adapter 接入 Gateway 或保持独立（均可）。
- 新增一个 channel adapter 不需要修改 Agent 核心代码。

---

## 主线 B：教育平台

> 主线 A 的 A5 完成后（即 Agent HTTP API 可用、channel 机制就绪），正式启动主线 B。

### B1 — 平台基础：用户身份与凭证码绑定

**核心任务**
- Next.js 项目初始化（App Router 目录结构、环境变量、**Prisma（或等价 ORM）+ PostgreSQL 迁移**、中间件与安全基线）。
- 用户模块：注册、登录（JWT 或 session，如 **Auth.js**）、角色（teacher/student/admin）。
- 凭证码模块：生成、有效期、绑定状态、撤销、重新生成。
- 绑定机制：学生/教师用凭证码绑定 Agent 身份，生成 channel identity mapping，注册到 Agent Gateway。
- Next.js 页面：登录、用户中心、凭证码管理。

**验收**
- 学生可以注册登录，生成凭证码，通过凭证码在 Agent 侧绑定身份。
- 绑定成功后，平台通过该 channel 向 Agent 发送消息时，Agent 能识别用户身份。
- 凭证码一次性生效，过期或已绑定后不可重用。

---

### B2 — 课程资料与 RAG 知识库

**核心任务**
- 课程模块：创建课程、编辑、可见状态、学生加入。
- 课时模块：课时 CRUD、与课程关联。
- 资料上传：支持 PDF/PPTX/Word/Markdown/TXT/图片，原始文件存储至 MinIO。
- 资料处理流水线：**Next.js 服务端**（Route Handlers / 独立 Node 脚本）创建处理任务推入 Redis 队列，Python Worker（rag_mvp）消费任务，执行 parse + RAG ingest，回写处理状态到 PostgreSQL。
- 课程 RAG 存储方案：每个课程对应 PostgreSQL 中一组独立 namespace 表（`course_{id}_entities` / `course_{id}_chunks` 等），使用 LightRAG PostgreSQL backend + pgvector。课程间知识图谱完全独立，不共享公共实体，不做跨课程联合图谱。
- 课程 RAG 与个人 RAG 是两套独立存储（PostgreSQL vs 本地 JSON/JSONL），由 Agent 侧的 `knowledge_query` 工具根据 session context 路由到正确来源。
- Next.js：课程详情页、课时列表、资料上传与状态展示。

**验收**
- 教师上传一个 PDF 后，资料状态依次流转：uploaded → parsing → parsed → indexing → ready。
- 学生在课程内向 Agent 提问，Agent 优先在该课程的 RAG 知识库中检索，回答时标注来源资料及来源类型（课程 / 个人）。
- 不同课程的资料互相隔离，跨课程查询不会返回其他课程内容。
- 课程 RAG 可被所有加入该课程的学生 Agent 共享索引，个人 RAG 仅本人可访问。

---

### B3 — 课程聊天界面与学习数据采集

**核心任务**
- Next.js：课程页嵌入聊天组件；通过 **Route Handler**（或等价服务端入口）**反向代理 Agent**，以 **SSE** 流式转发回复至浏览器。
- Agent 回答时携带课程上下文（course_id/lesson_id/user_id）。
- 埋点与数据采集：**Next.js 服务端**记录每次问答（用户、课程、问题、Agent 回答、命中资料、耗时、时间戳）。
- 教师数据面板：查看课程维度的问题列表、频繁薄弱点、学生活跃度。
- Next.js：教师端简单数据看板。

**验收**
- 学生在课程页面发问，回答流式显示，来源资料可展开查看。
- 每次问答在平台数据库中有完整记录（包括命中的资料 chunk ID）。
- 教师可以看到本课程学生提问数、最近问题列表。

---

## 阶段节奏示意

```
A1 ──► A2 ──► A3 ──► A4 ──► A5
                               │
                               ▼
                         B1 ──► B2 ──► B3
```

各 A 阶段顺序推进，B 阶段在 A5 完成后启动，B1-B3 可根据资源适当并行。

---

## 平台技术栈备忘

| 层 | 技术 | 用途 |
|---|---|---|
| 教育平台应用 | **Next.js**（App Router，Node 运行时） | 页面与路由、Route Handlers / Server Actions、SSE 代理、业务与鉴权 |
| 数据访问与迁移 | **Prisma**（或 Drizzle 等）+ PostgreSQL | 业务表结构、类型安全查询、迁移 |
| Agent 服务 | Python FastAPI | Agent HTTP API、RAG Worker |
| UI 组件（可选） | Ant Design 等 | 管理端、课程页、聊天布局 |
| 关系型 + 向量 DB | PostgreSQL + pgvector | 业务数据 + 课程 RAG 存储 |
| 任务队列 / 缓存 | Redis | 资料处理任务队列、LLM 响应缓存 |
| 对象存储 | MinIO（或本地 filesystem） | 原始资料文件存储 |
| 个人 RAG | 本地 JSON/JSONL | 用户自有资料，仅本人 Agent 可访问 |
| 课程 RAG | PostgreSQL namespace 隔离 | 课程资料，课程内学生共享，课程间独立 |

---

## 暂不纳入当前路线图

- 作业发布与自动批改闭环
- 微信 / 飞书正式生产 channel 接入（A5 Gateway 就绪后作为独立 channel 接入即可）
- 多租户商业化权限体系
- 移动端 App
- 直播实时转录与实时提问
- 复杂 BI 看板
