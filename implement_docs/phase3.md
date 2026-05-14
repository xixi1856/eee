# Phase 3 — TypeScript Agent 核心

## 目标
用 TypeScript 重新实现 ReAct Agent 核心，包含：
session 管理、ReAct 循环、工具注册、skills 加载、SubAgent 隔离执行。
Python 缩减为纯 RAG 微服务，暴露 HTTP API。

**依赖**：Phase 2 完成（TS Agent 直接读 PostgreSQL RAG 数据）

## 架构变化

```
旧：Next.js → (HTTP SSE) → Python EduAgent → (HTTP) → Python rag_mvp
新：Next.js API Routes (含 TS EduAgent) → (HTTP) → Python RAG Service
                                         → (直接) → PostgreSQL
```

## 执行契约（分 3 子阶段）

---

### Sub-Phase 3A：Python RAG 微服务 API 化

#### 3A.1 新建 `src/rag_service/main.py`
基于 FastAPI，暴露以下端点：
```
POST /rag/ingest
  body: { user_id?, course_id?, source_type: "personal"|"course", content, filename }
  → { task_id }（异步）或 { ok: true }（同步）

POST /rag/query
  body: { source: "personal"|"course"|"enrolled_courses",
          user_id, accessible_course_ids?: string[],
          question, mode?: "hybrid"|"naive" }
  → { hits: [{content, source, score}], answer?: string }

POST /rag/build-mindmap
  body: { source, user_id, course_id?, topic }
  → { markdown: string, html: string }

POST /rag/generate-quiz
  body: { course_id, topic, count }
  → { questions: [...] }

POST /rag/eval
  body: { question: string, answer: string, reference?: string }
  → { score: number, feedback: string }
```

鉴权：`X-Internal-Key: {RAG_SERVICE_API_KEY}`（新增环境变量，仅内网调用）

#### 3A.2 删除 `src/edu_agent/` 中对 rag_mvp 的直接 Python import
工具层通过 HTTP 调用 RAG Service，不再 `import rag_mvp.*`

#### 3A.3 Docker Compose 更新
`edu-platform/docker-compose.yml` 新增 `rag-service` 服务：
```yaml
rag-service:
  build: ../  # Dockerfile 指向 Python monorepo
  command: uv run rag-service
  ports:
    - "8001:8001"  # 仅内网
  environment:
    - DATABASE_URL
    - LIGHTRAG_PG_DSN
    - RAG_SERVICE_API_KEY
```

---

### Sub-Phase 3B：TypeScript Agent Core

新建目录 `edu-platform/lib/agent/`，包含：

#### 3B.1 `lib/agent/types.ts`
定义核心类型：
```typescript
type Message = { role: "user"|"assistant"|"tool", content: string, ... }
type Tool = { name: string, description: string, parameters: JSONSchema, execute: fn }
type AgentConfig = { model, systemPrompt, tools, maxIterations, userId, courseIds }
type TurnContext = { userId, accessibleCourseIds, courseId, lessonId }
```

#### 3B.2 `lib/agent/tool-registry.ts`
```typescript
class ToolRegistry {
  register(tool: Tool): void
  get(name: string): Tool | undefined
  getSchemas(): OpenAITool[]
}
```

#### 3B.3 `lib/agent/react-loop.ts`
ReAct 主循环（仿 Python `agent.py` 逻辑）：
- 最大 N 轮（可配置）
- 每轮：LLM call → parse tool_calls → execute → append tool result → repeat
- 支持 SSE 流式输出（`ReadableStream`）
- 异常处理：tool 执行失败 → 追加 error message 继续循环

**B3 SSE 输出协议**：

`ReadableStream` 输出的每个 `data:` 节点应符合 `B3SseEvent` 类型（已在 `lib/services/chatService.ts` 定义）：
```typescript
// 文本输出（可多次）
{ type: "text", content: "...一段输出..." }

// 工具调用（开始时发送）
{ type: "tool_call", name: "knowledge_query", tool_call_id: "..." }

// 工具结果（完成时发送）
{ type: "tool_result", name: "knowledge_query", success: true, duration_ms: 230 }

// 引用（knowledge_query 类工具在 tool_result 之前展开）
{ type: "citation", chunk_id: "...", source_label: "...", chunk_text: "..." }

// 结束（每个会话最后发送一次）
{ type: "done", tokens: 1200, exec_time_ms: 3400 }

// 追踪（可选，调试用）
{ type: "trace", trace_id: "...", event: "turn_start", turn_id: "...", ts: "..." }
```

`chatService.ts` 的 `createB3SseTransformFromAgent` 依赖此格式解析各字段。**TS ReAct 循环必须抚发同样的事件序列，不得將工具结果内嵌入 `text` 事件。**

#### 3B.4 `lib/agent/prompt-builder.ts`

对应 Python `prompt_builder.py`，负责将学习者画像和课程信息注入 system prompt：
```typescript
class PromptBuilder {
  // 从 MemoryCoordinator.buildRetrievedMemoryBlock() + LearnerProfile 构建完整 system prompt
  buildSystemPrompt(
    basePrompt: string,
    memoryBlock: string,
    profile: LearnerProfile | null,
    ctx: TurnContext,
  ): string
}
```

集成到 `react-loop.ts`：每轮循环开始前调用 `buildSystemPrompt()` 生成动态 system 消息。

#### 3B.5 `lib/agent/context-manager.ts`

对应 Python `context/calculator.py` + `context/compressor.py`：
```typescript
class ContextManager {
  // token 估算（基于字符数 / GPT-4 简化公式就开，有需要再引入 tiktoken WASM）
  estimateTokens(messages: Message[]): number

  // 嵌入初提示 + 最新 K 条 + 中间应用摨要压缩
  compress(
    messages: Message[],
    maxTokens: number,
  ): Promise<Message[]>
}
```

集成到 `react-loop.ts`：每次 LLM 调用前检查 token 上限，超限时自动压缩历史。

#### 3B.6 `lib/agent/session-store.ts`
```typescript
class SessionStore {
  // 使用 Redis（复用现有连接）存储 Message[]
  // key: `agent:session:{sessionId}`，TTL 24h
  async get(sessionId: string): Promise<Message[]>
  async append(sessionId: string, messages: Message[]): Promise<void>
  async reset(sessionId: string): Promise<void>
}
```

#### 3B.7 `lib/agent/skills-loader.ts`
读取 `skills/*.md` 文件，注册为特殊 tool（调用时注入 skill prompt 作为 system 上下文）

#### 3B.8 `lib/agent/memory/` — A3 Memory Stack TS 实现

对应 Python 的 `src/edu_agent/memory/` 目录，包含以下模块：

**`lib/agent/memory/types.ts`**
```typescript
type FactCategory = "concept_mastery"|"concept_confusion"|"preference"|"difficulty"|"question"|"achievement"
type Fact = { id, userId, sessionId, timestamp, category: FactCategory, content, confidence, sourceJson, metadata }
type Concept = { id, userId, name, description, masteryLevel, lastUpdated, supportingFactIds, relatedConcepts }
type LearnerProfile = { userId, profile: Record<string, unknown>, updatedAt }
```

**`lib/agent/memory/memory-store.ts`**
直接使用 Prisma 读写 Phase 2 新增的三张表：
```typescript
class MemoryStore {
  async addFact(fact: Omit<Fact, "id">): Promise<void>
    // → prisma.userMemoryFact.create()

  async listFacts(userId: string, since?: Date): Promise<Fact[]>
    // → prisma.userMemoryFact.findMany({ where: { userId, timestamp: { gte: since } } })

  async saveConcept(concept: Omit<Concept, "id">): Promise<void>
    // → prisma.userMemoryConcept.upsert({ where: { userId_name } })

  async listConcepts(userId: string): Promise<Concept[]>
    // → prisma.userMemoryConcept.findMany({ where: { userId } })

  async saveProfile(userId: string, profile: Record<string, unknown>): Promise<void>
    // → prisma.userLearningProfile.upsert()
}
```

**`lib/agent/memory/memory-retriever.ts`**
对应 Python `retriever.py`（TF-IDF 关键词匹配，无 embedding）：
```typescript
class MemoryRetriever {
  // 从 listConcepts() 结果中按 TF-IDF 排序，返回最相关的 N 个 Concept
  getRelevantConcepts(userId: string, query: string, maxResults?: number): Promise<Concept[]>
}
```

**`lib/agent/memory/memory-extractor.ts`**
对应 Python `extractor.py`（LLM 从会话记录提取 Fact 数组）：
```typescript
class MemoryExtractor {
  // 将 Message[] 转为 transcript 文本，调用 LLM 返回 Fact[]
  extractFactsFromSession(userId: string, sessionId: string, messages: Message[]): Promise<Fact[]>
}
```

**`lib/agent/memory/memory-consolidator.ts`**
对应 Python `consolidator.py`（Fact → Concept → Profile 聚合，含冲突处理）：
```typescript
class MemoryConsolidator {
  // 读取 Facts → 聚合更新 Concepts（conflict: concept_mastery vs concept_confusion 取 recency-weighted）
  // → 更新 LearnerProfile snapshot
  consolidateSession(userId: string, sessionId: string, messages: Message[], opts): Promise<void>
}
```

**`lib/agent/memory/memory-coordinator.ts`**
对应 Python `coordinator.py`（供 ReAct 循环调用的入口）：
```typescript
class MemoryCoordinator {
  // 每轮 ReAct 开始前调用，返回注入 system prompt 的记忆上下文文本
  buildRetrievedMemoryBlock(userId: string, userHint: string): Promise<string>

  // 判断是否达到 consolidation 阈值（token 数 >= extraction_min_session_tokens）
  shouldRunConsolidation(messages: Message[]): boolean

  // 触发 extractor + consolidator 流水线（异步，不阻塞主循环）
  consolidateSession(userId: string, sessionId: string, messages: Message[]): Promise<void>
}
```

**与 `react-loop.ts` 的集成点**：
1. 每轮循环开始：`coordinator.buildRetrievedMemoryBlock()` → 追加到 system prompt
2. 每轮循环结束后：检查 `coordinator.shouldRunConsolidation()` → 异步触发 `consolidateSession()`（不 await，不阻塞 SSE 流）

#### 3B.9 `lib/agent/subagent.ts`
SubAgent：独立 ToolRegistry 白名单 + 独立 ReAct 循环 + 不共享父会话历史

---

### Sub-Phase 3C：工具迁移（逐个迁移，可分 PR）

每个工具迁移到 `lib/agent/tools/`：

| Python 工具 | TS 文件 | 依赖 |
|---|---|---|
| `rag.py` → knowledge_query | `tools/rag.ts` | 调用 RAG Service HTTP API |
| `search.py` → web_search | `tools/search.ts` | Tavily SDK（有 Node.js 版本）|
| `memory.py` → read/write_profile | `tools/memory.ts` | `MemoryStore`（Prisma，直接读写三张记忆表）|
| `eval.py` → evaluate_answer | `tools/eval.ts` | 调用 RAG Service `/rag/eval` |
| `scheduling.py` → schedule_task | `tools/scheduling.ts` | 复用现有 Redis cron |
| `delegation.py` → delegate | `tools/delegation.ts` | 调用 SubAgent |
| `skills.py` → invoke_skill | `tools/skills.ts` | SkillsLoader |
| `ocr.py` → ocr_image | `tools/ocr.ts` | 调用 LLM vision API |
| `files.py` → file_ops | `tools/files.ts` | MinIO SDK |
| generate_quiz（rag_mvp）| `tools/quiz.ts` | 调用 RAG Service `/rag/generate-quiz` |
| build_mindmap（rag_mvp）| `tools/mindmap.ts` | 调用 RAG Service `/rag/build-mindmap` |

> `memory.py` 的工具层（`read_profile` / `write_profile`）直接调用 `MemoryStore`，
> 记忆的**自动提取和聚合**（`MemoryExtractor` + `MemoryConsolidator`）由 `react-loop.ts` 在每轮结束后异步触发，不需要额外工具调用。

**迁移顺序**：rag → memory → eval → search → 其余（按使用频率排序）

---

### Sub-Phase 3C-bis：Cron 迁移

将 Python `src/edu_agent/tools/scheduling.py`（定时任务管理）完全迁移到 Next.js：

#### 新增 `lib/agent/cron-scheduler.ts`
```typescript
// 复用现有 Redis cron 表（已有 data/cron_jobs.json 了解现有结构）
class CronScheduler {
  async schedule(userId: string, task: CronJob): Promise<string>  // 返回 job_id
  async cancel(jobId: string): Promise<void>
  async list(userId: string): Promise<CronJob[]>
}
```

#### 新增 `app/api/v1/internal/cron/route.ts`
```typescript
// Vercel cron 回调端点（也可用 internal key 保护）
// 请求头必须包含： Authorization: Bearer {CRON_INTERNAL_KEY}
GET /api/v1/internal/cron   → 计算到期任务 → 触发 agent 执行
```

`CRON_INTERNAL_KEY` 新增到环境变量列表。

---

### Sub-Phase 3D：切换 Chat API Route

修改 `app/api/v1/.../chat/route.ts`：
- 删除对 `agentClient.ts`（旧的 Python agent HTTP 调用）的依赖
- 改为直接调用 `lib/agent/react-loop.ts`
- TurnContext 从 JWT + `getAccessibleCourseIds()` 构建（复用 Phase 1 的函数）

#### 修改 `lib/services/chatService.ts` 的 SSE 转发逻辑

`courseChatSseResponse` 和 `qaCenterSseResponse` 的内部实现替换：
```typescript
// 旧：调用 Python agent HTTP SSE
// const agentStream = await postChatCompletionsStream(...)

// 新：直接运行 TS ReAct 循环
// const agentStream = reactLoop.run(messages, config)
```

`createB3SseTransformFromAgent` 已运作于 B3 SSE 格式，无需修改，但必须确保 TS 循环输出的事件序列与 3B.3 中定义的 B3 协议一致。
输出格式保持不变，前端不需任何改动。

---

### Sub-Phase 3E：退役 Python Agent

确认所有工具已迁移到 TS 后：
- 删除 `src/edu_agent/` 目录（或归档到 `src/edu_agent_deprecated/`）
- 保留 `src/rag_mvp/`（仍被 rag-service 使用）
- 更新 `docker-compose.yml`：删除 `edu-agent` 服务，保留 `rag-service`
- 删除 `edu-platform/lib/agentClient.ts`

## 执行后验证方法

### 验证 3A：RAG Service 独立运行
```bash
uv run rag-service
curl -X POST http://localhost:8001/rag/query \
  -H "X-Internal-Key: test" \
  -d '{"source":"course","user_id":"...","course_id":"...","question":"测试"}'
# 应返回 { hits: [...] }
```

### 验证 3B：TS Agent 单元测试
```bash
cd edu-platform
npx vitest run lib/agent/
# react-loop 测试：mock LLM + mock tools → 验证工具调用和循环终止
# session-store 测试：Redis mock → 读写验证
# memory-store 测试：Prisma mock → Fact append-only、Concept upsert
# memory-coordinator 测试：mock store → buildRetrievedMemoryBlock 返回正确文本块
```

### 验证 3C：逐工具集成测试
每迁移一个工具，新增对应 `__tests__/agent-tool-*.test.ts`

### 验证 3D：E2E Chat 流程
1. 启动 edu-platform（含 TS agent）+ rag-service（Python）
2. 登录学生 → 课程页 → 发送问题 → 触发 knowledge_query
3. 确认 SSE 流正常输出，工具调用在 UI 中可见
4. 确认 Python edu-agent 进程已**不再启动**
5. 发送多轮对话后确认 `user_memory_facts` 表有新增记录（记忆自动提取触发）
6. 再次发送消息，确认 system prompt 中包含上轮提取的知识点（记忆注入生效）

### 验证 3E：Python Agent 已完全退役
```bash
# docker-compose up 后确认无 edu-agent 容器
docker-compose ps
# 应只有: postgres, redis, minio, edu-platform, rag-service
```