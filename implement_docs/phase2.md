# Phase 2 — RAG 迁移到 PostgreSQL + Neo4j

## 目标
将 LightRAG 的文件系统存储（`vdb_*.json`、`kv_store_*.json`、`.graphml`）
切换到 PostgreSQL + pgvector（KV / 向量）+ Neo4j（图），
个人 KB 和课程 KB 统一用 `workspace` 值隔离。同时将学习者画像迁移到 PostgreSQL 表。

**依赖**：Phase 1 已完成（user_id 是稳定主键）

## 存储分工
| 数据类型 | 存储 | 隔离方式 |
|---|---|---|
| KV（全文、chunks、实体、关系缓存）| PostgreSQL + pgvector | `WHERE workspace = $1`（所有表带 workspace 列，PK 为 `(workspace, id)`）|
| 向量索引（chunks / entities / relations）| PostgreSQL + pgvector | 同上 |
| doc_status | PostgreSQL | 同上 |
| 知识图谱（节点 + 边）| Neo4j | 节点 Label = workspace 值，所有 Cypher 查询带 `` MATCH (n:`{workspace}`) ``|

## workspace 命名规范
| KB 类型 | workspace 值 |
|---|---|
| 课程 KB | `course_{course_id_下划线替换连字符}` |
| 个人 KB | `personal_{user_id_下划线替换连字符}` |

## 前置条件
- `docker compose up -d` 启动 postgres（pgvector/pgvector:pg16）和 neo4j（neo4j:5-community）
- `postgres/init.sql` 已包含 `CREATE EXTENSION IF NOT EXISTS vector`
- 现有文件系统 RAG 数据**不做迁移**（新系统从零开始）

## 执行契约

### 1. 基础设施确认

#### 1.1 pgvector 扩展
`postgres/init.sql` 已包含（Phase 2 前确认）：
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

#### 1.2 Neo4j 容器
`edu-platform/docker-compose.yml` 已新增 neo4j 服务，
`NEO4J_AUTH=neo4j/edu_neo4j_password`，Bolt 端口 7687。

### 2. Python 侧修改

#### 2.1 新增环境变量（`.env` + `docker-compose.yml` agent 服务）
```
LIGHTRAG_PG_DSN=postgresql://edu:edu@localhost:5432/edu_platform
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=edu_neo4j_password
```

#### 2.2 修改 `src/rag_mvp/engine.py` 中的 `LightRAG()` 构造调用

> **详注**：`kv_storage`、`vector_storage`、`doc_status_storage`、`graph_storage` 是 `LightRAG()` 的
> 构造函数参数，不是 `Settings` 类的字段。**不要修改 `config.py`**，
> 直接修改 `engine.py` 中的构造调用（即下方 §2.3）。

如果 `config.py` 中已存在 `graph_storage: str` 字段，可保留作可选配置，但实际传入 `LightRAG()` 的值必须固定为以下：
```python
graph_storage   = "Neo4JStorage"
kv_storage      = "PGKVStorage"
vector_storage  = "PGVectorStorage"
doc_status_storage = "PGDocStatusStorage"
```

#### 2.3 修改 `src/rag_mvp/engine.py` — `get_course_rag_anything()`
```python
# workspace 命名（- 替换为 _ 保证合法标识符）
workspace = f"course_{course_id.strip().lower().replace('-', '_')}"

rag = LightRAG(
    working_dir=str(settings.working_dir / "pg_layout"),  # 仅用于临时文件，不存图
    workspace=workspace,
    llm_model_func=llm_model_func,
    embedding_func=emb,
    kv_storage="PGKVStorage",
    vector_storage="PGVectorStorage",
    graph_storage="Neo4JStorage",
    doc_status_storage="PGDocStatusStorage",
    **_lightrag_insertion_tuning_kwargs(),
    **_lightrag_constructor_extras(),
)
```

> **LightRAG PG 表自动创建**：首次初始化 `LightRAG()` 时，PGKVStorage、
> PGVectorStorage、PGDocStatusStorage 会自动在 PostgreSQL 中创建所需的 8 张表
> `LIGHTRAG_*`，前提是 `LIGHTRAG_PG_DSN` 指向的数据库已存在且 pgvector 扩展已开启。
> **无需手动执行任何 SQL**，初始化一次后青就数据入库。

#### 2.4 修改个人 KB 初始化（`src/edu_agent/tools/rag.py` 或对应初始化函数）
```python
# user_id 来源：平台 JWT 的 sub（即 Prisma User.id UUID）
# 由 HTTP channel 从 X-Platform-User-Id header 解析后嵌入 TurnRuntimeContext

workspace = f"personal_{user_id.replace('-', '_')}"
personal_working_dir = settings.working_dir / "personal" / user_id.replace('-', '_')

rag = LightRAG(
    working_dir=str(personal_working_dir),  # 仅用于临时文件
    workspace=workspace,
    kv_storage="PGKVStorage",
    vector_storage="PGVectorStorage",
    graph_storage="Neo4JStorage",
    doc_status_storage="PGDocStatusStorage",
    ...
)
```

> `workspace = personal_{user_id_连字符替换下划线}`，与课程 KB 的隔离方式完全一致。

#### 2.5 删除 `rag_storage/course_graphs/` 目录的写入代码
切换 backend 后确认不再有代码向 `course_graphs/` 或 `personal_*/` 文件目录写入。
旧文件保留（归档），不删除。

### 3. 学习者画像迁移

#### 3.1 新增 Prisma Model
在 `prisma/schema.prisma` 新增：
```prisma
model UserLearningProfile {
  id        String   @id @default(uuid())
  userId    String   @unique
  user      User     @relation(fields: [userId], references: [id])
  profile   Json     // 存放原 learner_profile.json 的内容结构
  updatedAt DateTime @updatedAt

  @@map("user_learning_profiles")
}
```
执行 `npx prisma migrate dev --name add_user_learning_profile`

#### 3.2 修改 `src/edu_agent/learner_profile.py`
将读写从本地 JSON 文件改为 PostgreSQL：
- 读：`GET /api/v1/internal/learning-profile?user_id=`（新增平台内部端点）
- 写：`PATCH /api/v1/internal/learning-profile`（新增）
- 或：直接用 psycopg 连接同一 PostgreSQL（agent 有 DATABASE_URL 环境变量）

### 4. 记忆模块迁移（A3 Memory Stack）

记忆系统由三层组成，全部从文件系统迁移到 PostgreSQL：

```
Fact（原子事实，append-only）
  → Concept（知识点掌握度聚合）
    → LearnerProfile（用户画像快照）
```

#### 4.1 新增 Prisma Model — `UserMemoryFact`
```prisma
model UserMemoryFact {
  id         String   @id @default(uuid())
  userId     String
  sessionId  String
  timestamp  DateTime @default(now())
  category   String   // concept_mastery | concept_confusion | preference | difficulty | question | achievement
  content    String
  confidence Float
  sourceJson Json     // FactSource { session_id, message_id, tool_call_id?, tool_name? }
  metadata   Json     @default("{}")

  user User @relation(fields: [userId], references: [id])

  @@index([userId, timestamp])
  @@index([userId, sessionId])
  @@map("user_memory_facts")
}
```

设计原则：**append-only**，不做 UPDATE，与 Python 侧保持一致。

#### 4.2 新增 Prisma Model — `UserMemoryConcept`
```prisma
model UserMemoryConcept {
  id                 String   @id @default(uuid())
  userId             String
  name               String
  description        String   @default("")
  masteryLevel       Float    @default(0)
  lastUpdated        DateTime @updatedAt
  supportingFactIds  String[] // fact id 数组
  relatedConcepts    String[] // concept name 数组
  metadata           Json     @default("{}")

  user User @relation(fields: [userId], references: [id])

  @@unique([userId, name])
  @@index([userId])
  @@map("user_memory_concepts")
}
```

upsert 语义：`userId + name` 联合唯一，每次 consolidation 后更新 `masteryLevel`。

#### 4.3 `UserLearningProfile`（已在 §3 定义）
覆盖第三层，无需额外新增。

#### 4.4 修改 `src/edu_agent/memory/storage.py`
将 `MemoryStore`（文件系统）替换为 PostgreSQL 实现：

- `add_fact(fact)` → `INSERT INTO user_memory_facts`
- `list_facts(user_id, since?)` → `SELECT ... WHERE user_id = $1 ORDER BY timestamp`
- `save_concept(concept)` → `INSERT ... ON CONFLICT (user_id, name) DO UPDATE`
- `list_concepts(user_id)` → `SELECT ... WHERE user_id = $1`
- `save_profile(profile)` → upsert `user_learning_profiles`

连接方式：直接使用 `DATABASE_URL` 环境变量（psycopg3 异步连接，复用已有 PG 实例）。

执行 migration：
```bash
cd edu-platform
npx prisma migrate dev --name add_memory_stack
```

### 5. 会话存储迁移到 Redis

现有 `sessions/store.py` 用 JSONL 文件存储会话历史。  
此阶段明确迁移到 Redis，为 Phase 3 TS 重写做对齐（Phase 3B 的 `SessionStore` 使用同一 Redis key 规范）。

#### 5.1 Key 规范
```
agent:session:{session_id}   → JSON 序列化的 Message[] 列表
TTL：24h（可配置）
```

#### 5.2 修改 `src/edu_agent/sessions/store.py`
将文件读写替换为 Redis `GET` / `SET`（JSON 序列化），复用已有 `REDIS_URL` 环境变量。

### 6. 环境变量汇总
```
# Python agent / rag_mvp 侧新增
LIGHTRAG_PG_DSN=postgresql://edu:edu@localhost:5432/edu_platform
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=edu_neo4j_password
```

### 7. 旧文件系统数据过渡说明

A3 记忆三层切换到 PG 后，旧文件处理方式：

| 目录 | 处置 |
|---|---|
| `memory/facts/{user_id}/*.jsonl` | 保留不删除（归档），Phase 3 TS 重写后直接从 PG 读 |
| `memory/concepts/{user_id}.json` | 同上 |
| `memory/profiles/{user_id}.json` | 同上 |
| `rag_storage/course_graphs/` | 归档，不再写入 |
| `rag_storage/personal_*/` | 归档，不再写入 |

**不进行历史数据过够**：Phase 2 切换后必须持续使用的应用从平台 PG 全量构建暖机，
旧 JSONL / JSON 内容属于当前展开星期的训练数据，暂不迁移。

## 执行后验证方法

### 验证 1：pgvector 扩展存在
```sql
-- psql 连接后执行
SELECT extname FROM pg_extension WHERE extname = 'vector';
-- 应返回一行
```

### 验证 2：LightRAG PG 表自动创建
```sql
\dt "LIGHTRAG_*"
-- 应列出 LIGHTRAG_VDB_CHUNKS、LIGHTRAG_DOC_CHUNKS 等 8 张表
-- 每张表有 workspace 列，确认：
SELECT DISTINCT workspace FROM "LIGHTRAG_VDB_CHUNKS" LIMIT 10;
```

### 验证 3：Neo4j 节点 Label 隔离
```cypher
-- Neo4j Browser（http://localhost:7474）执行
CALL db.labels()
-- 应列出 course_xxx 和 personal_xxx 格式的 Label

-- 确认跨 workspace 不互串（某课程的节点不出现在另一个查询里）
MATCH (n:`course_abc`) RETURN count(n)
```

### 验证 4：课程 KB ingest
```bash
# 上传一个测试 PDF 到某课程，等待 INDEXING → READY
# 查询 LIGHTRAG_VDB_CHUNKS 表：
# SELECT count(*) FROM "LIGHTRAG_VDB_CHUNKS" WHERE workspace = 'course_{id}';
```

### 验证 5：knowledge_query 检索
```python
result = await knowledge_query(sources="course", question="测试问题", course_id="...")
assert len(result["hits"]) > 0
```

### 验证 6：个人 KB
```bash
# 通过 agent 工具调用 ingest_document 上传文件
# SELECT count(*) FROM "LIGHTRAG_VDB_CHUNKS" WHERE workspace = 'personal_{user_id}';
# 再调用 knowledge_query(sources="personal") 验证检索
```

### 验证 7：学习者画像读写
```bash
# 触发一次会话，agent 更新学习画像
# SELECT profile FROM user_learning_profiles WHERE user_id = '...';
```

### 验证 8：记忆三层写入
```sql
-- 触发一次含实质对话的会话后：
SELECT count(*) FROM user_memory_facts WHERE user_id = '...';
-- 应 > 0（Fact 已写入）

SELECT name, mastery_level FROM user_memory_concepts WHERE user_id = '...';
-- 应列出被提取的知识点

SELECT profile FROM user_learning_profiles WHERE user_id = '...';
-- 应有更新的画像快照
```

### 验证 9：会话历史在 Redis
```bash
redis-cli get "agent:session:{session_id}"
# 应返回 JSON 序列化的消息数组，而非读取 JSONL 文件
```

### 验证 10：文件系统残留清理
确认 `rag_storage/course_graphs/`、`rag_storage/personal_*/`、
`memory/facts/`、`memory/concepts/`、`memory/profiles/` 目录
不再被新代码写入（旧文件保留归档，不删除）

## 回滚方案
- `config.py` 中 `graph_storage` / `kv_storage` / `vector_storage` 改回原值即可回退到文件存储
- 建议保留旧的 `NetworkXStorage` 代码路径不删除，Phase 3 完成后统一清理