# Phase 4 — 生产加固与可观测性

## 目标
在 Phase 3 完成（TS Agent 全面接管）之后，对系统进行生产级加固：
性能调优、可观测性接入、限流防护、容灾预案。

**依赖**：Phase 3 完成（TS Agent 稳定运行，Python RAG Service 独立部署）

---

## 执行契约

### 1. PostgreSQL 性能调优

#### 1.1 pgvector 索引策略
LightRAG PG adapter 自动创建 `LIGHTRAG_VDB_*` 表，默认使用精确检索（无 ANN 索引）。
课程数据量超过 10 万向量后需手动建索引：

```sql
-- HNSW（推荐，支持增量插入）
CREATE INDEX CONCURRENTLY ON "LIGHTRAG_VDB_CHUNKS"
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- 或 IVFFlat（批量数据更快，但插入后需 VACUUM ANALYZE）
CREATE INDEX CONCURRENTLY ON "LIGHTRAG_VDB_CHUNKS"
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
```

每个 workspace（课程/个人）的向量表行数超 5 万时触发建索引流程。

#### 1.2 慢查询监控
```sql
-- 开启慢查询日志（pg_statement_timeout）
ALTER SYSTEM SET log_min_duration_statement = 500;  -- 超 500ms 记录
SELECT pg_reload_conf();
```

在 Grafana 或日志聚合平台中添加告警规则：慢查询超 10 次/分钟触发告警。

#### 1.3 连接池调优
`DATABASE_URL` 添加连接池参数，避免 Next.js Serverless 下过多连接：
```
DATABASE_URL=postgresql://...?connection_limit=10&pool_timeout=30
```

Prisma 侧确认已启用 `pgBouncer=true`（如使用 PgBouncer 中间件）。

---

### 2. Neo4j 图查询缓存策略

#### 2.1 查询结果缓存
LightRAG `Neo4JStorage` 每次 `query()` 都执行全图遍历，在高并发下成本较高。
新增 Redis 缓存层（TTL 5 分钟）：

```typescript
// lib/cache/graph-cache.ts
async function cachedGraphQuery(
  workspace: string,
  query: string,
  fallback: () => Promise<unknown>,
): Promise<unknown> {
  const key = `graph:${workspace}:${hash(query)}`
  const cached = await redis.get(key)
  if (cached) return JSON.parse(cached)
  const result = await fallback()
  await redis.set(key, JSON.stringify(result), "EX", 300)
  return result
}
```

#### 2.2 Neo4j 内存配置
`docker-compose.yml` 中 neo4j 服务调整 JVM 堆大小（按部署机器内存设置）：
```yaml
environment:
  - NEO4J_server_memory_heap_initial__size=512m
  - NEO4J_server_memory_heap_max__size=2g
  - NEO4J_server_memory_pagecache__size=1g
```

---

### 3. TS Agent 可观测性

#### 3.1 结构化日志
在 `lib/agent/react-loop.ts` 中接入结构化日志（推荐 `pino`）：
```typescript
logger.info({
  event: "turn_complete",
  userId,
  sessionId,
  turnId,
  tools: toolCallsSummary,
  tokens,
  durationMs,
})
```

关键事件需记录：`session_start`、`turn_start`、`tool_call`、`tool_result`、`turn_complete`、`consolidation_triggered`。

#### 3.2 SSE 错误率监控
在 `courseChatSseResponse` / `qaCenterSseResponse` 中捕获并上报 SSE 中断事件：
- 客户端断连（`request.signal.aborted`）→ 记录 `sse_aborted`
- tool 执行超时（`AbortSignal.timeout`）→ 记录 `tool_timeout`
- LLM API 错误 → 发送 `{ type: "done", error: "..." }` 事件，记录 `llm_error`

目标指标：`sse_error_rate < 1%`，`p95 tool_duration_ms < 3000`。

#### 3.3 分布式追踪（可选）
集成 OpenTelemetry（`@opentelemetry/sdk-node`）：
- 每个 SSE 请求生成 trace_id
- ReAct 循环每轮作为 span
- 接入 Jaeger 或 Grafana Tempo

---

### 4. RAG Service 限流防护

#### 4.1 接入速率限制
在 RAG Service（FastAPI）中添加 per-user 限流：
```python
# 使用 slowapi（基于 Redis）
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=lambda req: req.headers.get("X-Platform-User-Id", "anonymous"))

@app.post("/rag/ingest")
@limiter.limit("10/minute")  # 每用户每分钟最多摄入 10 次
async def ingest(request: Request, body: IngestRequest): ...

@app.post("/rag/query")
@limiter.limit("60/minute")  # 每用户每分钟最多查询 60 次
async def query(request: Request, body: QueryRequest): ...
```

#### 4.2 摄入任务队列保护
摄入（ingest）为重型操作，设置全局并发上限：
```python
# 最多 4 个并发 ingest 任务（按 CPU 核数调整）
ingest_semaphore = asyncio.Semaphore(4)
```

---

### 5. 容灾与预案

#### 5.1 Neo4j 不可用降级
当 Neo4j 连接超时时，RAG Service 自动降级为"仅向量检索"模式（不做图遍历），
返回 `{ degraded: true }` 标志位，让 TS Agent 侧感知并调整响应措辞。

#### 5.2 Redis 不可用降级
- SessionStore：Redis 失败时退化为内存存储（仅当轮生效，不跨请求）
- CronScheduler：任务入队失败时返回错误，不静默丢失

#### 5.3 数据库备份策略
- PostgreSQL：每日全量备份（`pg_dump`），保留 7 天
- Neo4j：每日导出（`neo4j-admin database dump`），保留 7 天

---

## 执行后验证方法

### 验证 1：pgvector HNSW 索引效果
```sql
-- 对比建索引前后的查询计划
EXPLAIN ANALYZE
SELECT id, embedding <=> '[...]' AS dist
FROM "LIGHTRAG_VDB_CHUNKS"
WHERE workspace = 'course_xxx'
ORDER BY dist LIMIT 10;
-- 建索引后应出现 Index Scan using ... 而非 Seq Scan
```

### 验证 2：Neo4j 缓存命中率
```bash
redis-cli keys "graph:*" | wc -l
# 多次查询同一问题后，缓存键应存在
redis-cli ttl "graph:course_xxx:..."
# 应返回 0-300（未过期）
```

### 验证 3：结构化日志输出
```bash
# 发起一次聊天请求后检查 Next.js 日志
# 应包含 { event: "turn_complete", userId, tokens, durationMs }
```

### 验证 4：限流生效
```bash
# 对 /rag/query 发送超过 60 次/分钟的请求
for i in $(seq 1 65); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "X-Internal-Key: test" \
    -H "X-Platform-User-Id: test-user" \
    -X POST http://localhost:8001/rag/query \
    -d '{"source":"course","user_id":"test","question":"测试"}'
done
# 第 61+ 次应返回 429
```

### 验证 5：Neo4j 降级
```bash
# 停止 Neo4j 容器后发起查询
docker-compose stop neo4j
curl -X POST http://localhost:8001/rag/query ...
# 应返回 { hits: [...], degraded: true }，而非 500
```
