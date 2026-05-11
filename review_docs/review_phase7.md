# Phase 7 Architecture Compliance Audit

**基准**：[`implement_docs/phase7.md`](../implement_docs/phase7.md) 为唯一目标架构与实现契约。  
**审计对象**：当前仓库真实代码（`edu-platform/`、`src/rag_mvp/`、`src/edu_agent/`）。  
**说明**：历史稿中关于 **Redis List + BRPOP**、Worker **信任队列中的 `course_id`/`minio_path`** 的描述已 **过时**；当前实现已改为 **Redis Stream + DB 仅 `material_id`**，以本文件为准。

---

## 1. 当前系统架构（Current Architecture）

以下描述 **真实主路径**（以代码为准）。

### 1.1 Upload flow

1. 教师 `POST /api/v1/courses/{courseId}/materials`（multipart，`file` + 可选 `lesson_id`）。  
2. [`materialService.uploadMaterialStream`](edu-platform/lib/services/materialService.ts)：校验教师、`pdf`/`md`/`txt`、大小、`REDIS_URL`；创建 `Material`（`UPLOADED`）；流式 [`putObjectStream`](edu-platform/lib/minio.ts) 写入 MinIO 键 `materials/{courseId}/{materialId}/{safeName}`。  
3. [`enqueueRagTask`](edu-platform/lib/queue/ragTask.ts) 向 Redis Stream **`XADD`**：`task_id`、`material_id`、`operation`（`parse_and_index`）、`created_at`。**不**写入 `course_id` / `minio_path`。

### 1.2 MinIO flow

- 上传：Next.js 服务端直传 MinIO。  
- Worker：[`download_object_to_path`](src/rag_mvp/material_processor.py) 使用 **DB claim 返回的** `minio_path` 下载到本地临时路径（**不信任** Stream 业务字段）。

### 1.3 Queue flow

- Stream 名默认 `edu:rag:tasks:stream`（`RAG_TASK_STREAM_NAME` 可覆盖）。  
- [`worker.py`](src/rag_mvp/worker.py)：`XGROUP CREATE`（幂等）、**`XAUTOCLAIM`**（`RAG_STREAM_CLAIM_IDLE_MS`）+ **`XREADGROUP`**；`ValueError`（如缺 `material_id`）**ACK** 丢弃毒消息；其它异常 **不 ACK** 以便重投。

### 1.4 Worker flow

- `_process_one` 仅解析 `material_id`、`operation`。  
- `parse_and_index` → [`process_parse_and_index`](src/rag_mvp/material_processor.py)（见下）。  
- `delete_material` → [`process_delete_material`](src/rag_mvp/material_processor.py)（要求 `materials.is_deleted = true`，再删 LightRAG 文档）。

### 1.5 Parse flow（目标形态：与 `engine.parse_file` 同源）

- **契约要求**：解析与 CLI `rag parse` 所用 [`engine.parse_file`](src/rag_mvp/engine.py) / `_build_parser` + `parse_document` **同源**。  
- **实现收敛后**：Worker 在 claim + MinIO 下载后，先对落盘文件调用 **`parse_file` 等价路径**，将 `Material` 置为 **`PARSED`**，再进入索引阶段。

### 1.6 Ingest flow

- 在 **`INDEXING`** 状态下，将 MinerU 产物经 **绑定该课 `workspace` 的 `RAGAnything.insert_content_list`** 写入 PostgreSQL **`LIGHTRAG_*`**（与 `reindex_from_cache` 同范式），**避免**与解析阶段重复调用 `process_document_complete` 导致双次解析。  
- 成功：`READY` + `indexed_chunk_count`；失败：`FAILED` + `status_message`。

### 1.7 Query flow

- Agent [`knowledge_query`](src/edu_agent/tools/rag.py)：`course_id` **仅**来自 [`TurnRuntimeContext.course_id`](src/edu_agent/runtime_context.py)（工具 schema **无** `course_id`）。  
- 课程腿：`httpx` → [`GET /api/v1/internal/course-rag-access`](edu-platform/app/api/v1/internal/course-rag-access/route.ts) 校验选课后，调用 **`LightRAG.aquery_data`**（与 `engine` 个人侧 **同一数据面检索 API**）。  
- 个人腿：同一 **`aquery_data`** 路径（**不再**以 `engine.query` 整段 LLM 答案作为主检索结果）。

### 1.8 LightRAG usage

- 课程：`PGKVStorage`、`PGVectorStorage`、`PGDocStatusStorage`，`workspace = course_{lower(uuid)}`（[`course_id_to_workspace`](src/rag_mvp/course_workspace.py)）。  
- 个人：默认本地 `working_dir`（与课程 **PG 面分离**，符合 phase7「个人可 Json、课程 PG」边界）。

### 1.9 Workspace usage

- **唯一**派生函数 `course_id_to_workspace`；Worker ingest 与课程检索共用。

### 1.10 Agent routing

- `sources ∈ {personal, course, all}` 非法则 **tool_error**；`course`/`all` 且无绑定课程则报错。  
- **显式要求**：`sources` **必填**（省略则报错），与决策 5「非法即报错」一致。

### 1.11 Status transitions

- Claim：`UPLOADED` 或 `PARSING`/`INDEXING`/`PARSED` 且 **stale**（`RAG_MATERIAL_STALE_SEC`）→ `PARSING`。  
- 其后：`PARSING` →（解析成功）→ `PARSED` → `INDEXING` → `READY` / `FAILED`。

### 1.12 Deletion handling

- API：教师删除 → Prisma **`isDeleted: true`** → **`enqueueRagTaskWithRetry`**（最多 5 次退避重试）→ MinIO **`deleteObject`**；入队仍失败时写入 `statusMessage: RAG_DELETE_QUEUE_FAILED:…` 并返回 503（向量清理由后续补偿/运维处理）。  
- Worker：`delete_material` → `adelete_by_doc_id`（稳定 `doc_id`）。

---

## 2. 与 Phase 7 不一致的实现（Architecture Violations）

### Critical

## 状态机占位早于真实解析完成

### Current Implementation

（历史问题）在调用完整索引管线前即将 `Material` 标为 `PARSED`/`INDEXING`，与 MinerU 实际进度脱节。

### Expected Architecture

phase7 决策 2：`uploaded → parsing → parsed → indexing → ready` 与 **真实阶段**一致。

### Why This Is A Problem

枚举值误导运维与前端；崩溃恢复时状态与磁盘不一致。

### Risk

stale state；排障困难；与验收「状态流转」语义不符。

### Required Refactor

先完成 **与 `engine.parse_file` 同源** 的解析并落盘，再写 `PARSED`；仅在开始写入 `LIGHTRAG_*` 前写 `INDEXING`；成功后再 `READY`。

---

## Worker 曾未与 `engine.parse_file` 显式同源（已收敛方向）

### Current Implementation

（收敛前）仅依赖 `process_document_complete` 内嵌解析，与 CLI `rag parse` 的 **可测试边界** `parse_file` 不重合。

### Expected Architecture

phase7 清单项 2：解析 **委托** `engine` 与 `rag parse` **同一入口**。

### Why This Is A Problem

升级 MinerU / 调参时易漏改一侧，ingest/query 认知分裂。

### Risk

ingest/query 分叉；非幂等运维假设。

### Required Refactor

Worker 路径显式调用 `parse_file`（或 `engine` 暴露的异步包装），索引阶段仅 `insert_content_list` + 稳定 `doc_id`。

---

## 删除路径与向量/队列一致性

### Current Implementation

删除涉及 MinIO、DB 软删、Redis 入队；任一步失败可能产生短暂不一致。

### Expected Architecture

phase7：MinIO、`materials`、**`LIGHTRAG_*`** 最终一致；失败 **可观测**。

### Why This Is A Problem

单点失败会留下孤儿对象或不可见数据。

### Risk

vector inconsistency；合规与租户隔离风险。

### Required Refactor

入队 **重试** + 失败时写入 `statusMessage`；长期方案为 Outbox / 补偿任务（见第 5 节）。

---

### High

## 课程与个人检索语义曾双轨（已收敛方向）

### Current Implementation

（历史）课程 `aquery_data` vs 个人 `engine.query` 整段答案。

### Expected Architecture

双源 **并列**且 **同型 chunk 数据面**（决策 5 / 清单）。

### Why This Is A Problem

Agent 融合逻辑难以依赖统一字段。

### Risk

duplicated retrieval 语义；错误排序与引用。

### Required Refactor

个人与课程均走 **`LightRAG.aquery_data`** + 统一 `QueryParam` 映射。

---

## 工厂与 MinerU 参数重复

### Current Implementation

（历史）`course_lightrag` 与 `engine` 各自构造 `LightRAG`/`RAGAnything` 与 MinerU kwargs。

### Expected Architecture

phase7 清单 1：**单一工厂** + 单一 kwargs 源。

### Why This Is A Problem

embedding / 存储后端配置漂移。

### Risk

向量维度不一致；难以推理「哪套配置生效」。

### Required Refactor

`get_course_rag_anything` 等迁入 `engine`（或单一模块），`material_processor` **仅**从 `engine` 取 `mineru_kwargs()`。

---

### Medium

## `material_title` / 真实相关性分数

### Current Implementation

工具结果中 `material_title` 可能为空；课程 `relevance_score` 若仍用启发式则与 LightRAG 不一致。

### Expected Architecture

phase7 `QueryResult` 示例字段；分数来自引擎或明确缺失。

### Required Refactor

按 `material_id` 查 `materials.original_filename`；移除占位分数，改用 chunk 元数据中的分数（若有）。

---

### Low

- `materialService` 中 `extToFileType` 的 image 分支与 API 白名单并存（冗余）。  
- 内部鉴权 API 使用 `GET` + query：与契约兼容，保留。

---

## 3. 必须删除的旧架构（Legacy Structures To Remove）

- **文档/评审中** 关于 `BRPOP`、`edu:rag:tasks:pending`、队列内 **`course_id`/`minio_path`** 主路径的叙述（实现已移除）。  
- **`process_document_complete` 作为课程 Worker 唯一入口`**：在已具备 MinerU 缓存后，应 **禁止**再双次解析；索引仅 `insert_content_list`。  
- **个人 `knowledge_query` 主路径使用 `engine.query` 字符串**：与课程 chunk 腿不一致时应删除。  
- **重复的 `_mineru_kwargs` 实现块**：删除，统一 `engine.mineru_kwargs()`。

---

## 4. 目标统一架构（Target Unified Architecture）

- **Upload**：Route Handler → MinIO → `UPLOADED` → Stream **仅** `material_id` + `operation`。  
- **Parse**：Worker → DB claim → **`engine.parse_file` 同源解析** → `PARSED`。  
- **Ingest**：**唯一** `workspace` 绑定 `RAGAnything` → **`insert_content_list`** → `LIGHTRAG_*` → `READY`。  
- **Retrieval**：**唯一** `LightRAG.aquery_data`（或经 `engine` 薄封装）；**禁止**手写向量 SQL 主路径。  
- **Workspace**：**唯一** `course_id_to_workspace(course_id)`。  
- **Queue**：Stream + Group + `XAUTOCLAIM`；Worker **只信** `material_id`，其余 **`SELECT materials`**。  
- **Agent**：`course_id` **仅** `TurnRuntimeContext`；`sources` **必填**、非法报错；双源 **同型 chunk**。  
- **删除**：软删 + **入队重试** + MinIO；Worker 清 `LIGHTRAG_*`；失败可观测。

**单一主路径（口号）**：

`DB(materials) → engine.parse_file → insert_content_list(workspace) → LightRAG.aquery_data(workspace)`；队列只传 **id**。

---

## 5. 重构实施计划（Refactor Plan）

### Phase A（数据面与 Worker）

- [`material_processor.py`](src/rag_mvp/material_processor.py)：真实 `PARSED` / `INDEXING`；`parse_file` + `ingest_parsed` 拆分。  
- [`materialService.ts`](edu-platform/lib/services/materialService.ts)：删除路径入队 **重试** + 失败 `statusMessage`。  
- 测试：状态迁移与 ingest 路径（补充/更新 `tests/rag_mvp/`）。

### Phase B（单一工厂）

- [`engine.py`](src/rag_mvp/engine.py)：`mineru_kwargs()`、`get_course_rag_anything`、课程 ingest/query 核心。  
- [`course_lightrag.py`](src/rag_mvp/course_lightrag.py)：薄 re-export，避免重复初始化逻辑。

### Phase C（Agent 契约）

- [`rag.py`](src/edu_agent/tools/rag.py)：`sources` 必填；个人与课程 **同型** `aquery_data`；分源错误 **不丢弃**已成功课程块；可选 DB 查 `material_title`。  
- [`test_knowledge_query_phase7.py`](tests/edu_agent/test_knowledge_query_phase7.py)：覆盖缺省 `sources`、双源部分失败等。

---

## Hard Constraints 自检（收敛后目标）

| # | 约束 | 说明 |
|---|------|------|
| 1 | 禁止手写向量 SQL 主检索 | 保持 LightRAG API。 |
| 2 | 禁止按课拆表 | `workspace` 行级隔离。 |
| 3 | 禁止 parallel retrieval | 双源均 `aquery_data`。 |
| 4 | 禁止 duplicated ingest | parse / ingest 分离 + 单工厂 + 单 kwargs。 |
| 5 | 禁止与 `parse_file` 不同源 | Worker 显式 `parse_file`。 |
| 6 | 禁止 `course_id` 来自工具参数 | 保持 runtime-only。 |
| 7 | 禁止 fallback 旧逻辑 | 删除 `engine.query` 主路径。 |
| 8 | 禁止 API 层 embedding/chunking | 不变。 |
| 9 | 禁止静默忽略失败 | 删除入队重试 + 分源错误暴露。 |
| 10 | 禁止双架构长期共存 | `course_lightrag` → 薄封装。 |

---

*本文件随实现演进应更新「当前架构」小节以保持与代码一致。*
