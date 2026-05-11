# Phase 7：B2 课程资料与 RAG 知识库详细方案

## 目标与背景

B1 完成后，教育平台已经具备用户身份管理与 Agent 绑定能力。B2 的核心目标是建立**课程资料管理与 RAG 知识库系统**，使教育平台能够：

1. **课程与课时管理**：创建课程、编辑课程信息、学生加入、课时 CRUD。
2. **资料上传与处理**：API 侧可先仅开放 **PDF、Markdown、TXT**；**实现目标**为 Python Worker 的解析与索引 **与 `src/rag_mvp/engine.py` 中 CLI `rag parse` 对应的 `parse_file` / `parse_folder` 及后续 ingest 同源**——即统一走 **RAG-Anything（如 MinerU）** 的文档管线，**不**长期依赖与 `parse_file` 无关的独立轻量解析（如仅 `pypdf` 抽字）。其它格式（PPTX、Word、图片）在管线接入前不在 API 中开放上传。
3. **课程 RAG 存储**：每门课对应 LightRAG 的一个 **`workspace` 字符串**（由 **`course_id` 稳定派生、1:1**），向量与文档状态落在 **共享的 `LIGHTRAG_*` PostgreSQL 表** 中并按 `workspace` 行级隔离；ingest 走 **RAG-Anything + `LightRAG` PG 存储**，检索走 **`LightRAG.query` / multimodal**（与 `rag_mvp` 个人库同引擎范式）。详见 **决策 3**。
4. **个人 RAG 与课程 RAG 的路由**：Agent 侧的 `knowledge_query` 工具根据 session context 路由到正确来源。
5. **资料处理流水线**：**Next.js 服务端**（Route Handler 或服务模块）创建任务 → Redis 队列 → Python Worker 处理 → 回写状态。

B2 完成后，教育平台应该具备以下特征：

- 教师上传 PDF 后，资料状态依次流转：uploaded → parsing → parsed → indexing → ready。
- 学生在课程内提问，Agent 优先查课程 RAG，返回结果标注来源类型。
- 不同课程的资料互相隔离，跨课程查询不返回其他课程内容。
- 课程 RAG 可被所有加入该课程的学生共享。

## 架构决策

### 决策 1：课程与课时的数据模型

```sql
-- 课程表
CREATE TABLE courses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    cover_image_url VARCHAR(255),
    status ENUM('DRAFT', 'PUBLISHED', 'ARCHIVED') NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE
);

-- 课时表
CREATE TABLE lessons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES courses(id),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    order_index INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE
);

-- 学生选课表
CREATE TABLE course_enrollments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES courses(id),
    student_id UUID NOT NULL REFERENCES users(id),
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(course_id, student_id)
);

-- 资料表
CREATE TABLE materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES courses(id),
    lesson_id UUID REFERENCES lessons(id),
    original_filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(50) NOT NULL,  -- "pdf", "pptx", "docx", "md", "txt", "image"
    file_size INT NOT NULL,
    minio_path VARCHAR(255) NOT NULL,  -- MinIO 中的完整路径
    status ENUM('UPLOADED', 'PARSING', 'PARSED', 'INDEXING', 'READY', 'FAILED') NOT NULL,
    status_message TEXT,  -- 错误或处理进度描述
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    indexed_chunk_count INT DEFAULT 0,  -- RAG 索引后的 chunk 数
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX ON courses(teacher_id);
CREATE INDEX ON course_enrollments(student_id);
CREATE INDEX ON materials(course_id, status);
```

### 决策 2：资料处理流水线

```
1. 前端上传文件 → **Next.js Route Handler**（如 `POST .../materials`）接收
2. 存储原始文件到 MinIO
3. 创建 Material record（status=UPLOADED）
4. 生成处理任务，推入 Redis 队列
5. Python Worker（rag_mvp）消费任务：
   - 从 MinIO 下载文件
   - **解析（parse）**：须与 **`engine.parse_file` / `parse_folder`（即 CLI `rag parse` 所用入口）同源**——同一套 **RAG-Anything / MinerU** 配置与输出约定，再进入 ingest；**目标**是课程资料与个人 CLI 场景 **共用一条解析实现**，避免 Worker 私有的「仅抽文本」分叉。
   - 更新 Material record（status=PARSED）
   - **索引（ingest）**：在同一路径上接续 **绑定该课 `workspace` 的 `LightRAG` ingest**，写入 **`LIGHTRAG_*`（PGKV / PGVector / PGDocStatus / 可选 PGGraph）**（与 `engine.ingest_file` 等个人 ingest 同范式）
   - 更新 Material record（status=READY, indexed_chunk_count）
6. 处理失败时更新 status=FAILED，记录错误信息
7. Agent 可查询 READY 状态的资料
```

### 决策 3：课程 RAG 存储、PostgreSQL 形态与 LightRAG 检索

Phase 7 以 **LightRAG 官方 PostgreSQL 多租户模型** 为唯一落地方案：**共享元数据表名（`LIGHTRAG_*`）+ 行级 `workspace` 隔离**，**不按课拆表名**；业务上的「哪门课」一律通过 **`course_id` → `workspace` 字符串** 映射进入引擎。

#### 存储与表职责

- **平台业务表**（Prisma / Migrate）：`courses`、`materials`、`course_enrollments` 等，保存 **文件元数据、状态机、MinIO 路径**；**不**承担向量检索的专用 schema（避免与 LightRAG 内置 PG 布局并行维护一套「手写 chunk 表」）。
- **LightRAG PG 存储**：启用 **`PGKVStorage`、`PGVectorStorage`、`PGDocStatusStorage`**，可选 **`PGGraphStorage`**。表由 LightRAG 侧 DDL 管理（如 `LIGHTRAG_DOC_CHUNKS`、`LIGHTRAG_VDB_CHUNKS`、`LIGHTRAG_VDB_ENTITY`、`LIGHTRAG_VDB_RELATION` 等，具体以所用版本为准），主键形态为 **`(workspace, id)`**，**所有课程共用同一套 DDL**。

#### `course_id` 与 `workspace`（契约）

- 为每门课生成 **稳定、唯一** 的 **`workspace` 字符串**，与 **`courses.id`（`course_id`）1:1**（推荐例如 `course_<uuid>` 或规范化的 UUID 字符串）。应用层 **固定同一派生函数**，供 Worker ingest、`knowledge_query` 建实例复用。
- **`workspace` 在对应 `LightRAG` 实例初始化后不可变**（与 LightRAG 文档一致）。
- **`working_dir`**：在 Json 等文件型后端表示子目录；在 **本方案 PostgreSQL 后端** 下，**租户隔离以表内 `workspace` 列为准**；多门课可 **共享同一 `working_dir`、不同 `workspace`**。

#### 解析与 Ingest（写入格式）

- **解析**：Worker 必须改为调用与 **`engine.parse_file` / `parse_folder`**（CLI **`rag parse`**）**相同的 RAG-Anything 解析栈**（同一 `_build_parser` / `RAGAnything.parse_document` 或引擎封装的等价异步路径），保证 **输出目录与缓存格式** 与 `ingest_file` / `process_document_complete` 等 ingest 前置条件一致。
- **Ingest**：在同一 `process_parse_and_index`（或拆分后的清晰步骤）中，在解析产物就绪后，使用 **已绑定该课 `workspace`** 的 **`LightRAG` 实例** 执行与 **`engine.ingest_*`** 同源的 ingest，写入 **`LIGHTRAG_*`**。
- 嵌入模型与维度由 **LightRAG / 配置** 统一约束（默认与项目其余部分一致，如 Ollama `bge-m3`）；**不在应用层另写一套向量列 DDL**。

#### 检索（查询格式）

- **课程内检索入口**：`LightRAG.query`、`query_with_multimodal` 或 `rag_mvp` 引擎封装方法；**禁止**以手写 SQL 替代课程腿主路径（避免与 hybrid / 图 / 多模态能力分叉）。
- **Agent**：`knowledge_query` 在 **`sources` 含课程** 时，根据 runtime 中的 **`course_id`** 解析出 **`workspace`**，构造/获取对应 **`LightRAG` 实例** 并调用上述 API；平台 internal API **仅鉴权与审计**，不实现第二套向量检索 DSL。

#### 运维与删除

- 删除课程、删除资料：除 MinIO 与 **`materials` 行**外，须按 **`workspace`（及资料在 LightRAG 内的 doc/chunk 标识）** 调用引擎或存储层支持的删除策略，**不得**影响其他 `workspace` 行。

### 决策 4：资料处理任务的格式与队列（Redis Stream）

**不信任队列载荷中的业务字段**：消息体仅含 `material_id` 与 `operation`；`course_id`、`minio_path`、`file_type` 一律由 Worker **`SELECT materials`** 得到。

Stream 消息字段（示例）：

```json
{
  "task_id": "task_uuid",
  "material_id": "material_uuid",
  "operation": "parse_and_index",
  "created_at": "2026-05-10T09:00:00Z"
}
```

- Stream 键名默认：`edu:rag:tasks:stream`（`RAG_TASK_STREAM_NAME` 可覆盖）。
- Consumer group（默认 `edu-rag-workers`）+ **`XREADGROUP` / `XACK`**；超时未 ACK 的消息由 **`XAUTOCLAIM`** 回收（`RAG_STREAM_CLAIM_IDLE_MS`）。
- 资料状态机支持 **stale reclaim**（`RAG_MATERIAL_STALE_SEC`）：`PARSING`/`INDEXING`/`PARSED` 过久可重新抢占，避免静默卡死。

**已废弃**：Redis List + `BRPOP` + `edu:rag:tasks:pending`。

### 决策 5：Agent 侧的多源 RAG 查询

`knowledge_query`：**`course_id` 仅来自 `TurnRuntimeContext`（session 绑定）**，工具参数中 **不再** 暴露 `course_id`，避免模型侧覆盖租户边界。`sources` 非法值 **直接报错**（不回退为 `all`）；`sources` 为 `course` 或 `all` 时会话 **必须** 已绑定课程，否则报错（禁止隐式退回仅个人库）。`top_k` 须为 1–20 的整数。

```python
# 语义摘要（实际以 JSON Schema 为准）
# sources: personal | course | all
# course 边界: runtime context only
```

返回结果包含 `origin` 标注：

```python
class QueryResult(BaseModel):
    chunk_id: str
    text: str
    origin: Literal["personal", "course"]
    course_id: str | None  # 当 origin="course" 时
    material_id: str | None
    material_title: str | None
    relevance_score: float
```

**双源对齐**：返回结构中的 **`origin`** 标注 **平台课程 LightRAG 索引** 与 **本地 `rag_mvp` 个人库**；课程命中均由 **`LightRAG` 系 API** 产出（见决策 3）。

### 决策 6：Next.js 课程管理与资料上传

**`app/` 下页面**（Server / Client 组件组合）：

- **课程列表**：教师查看自己的课程 / 学生查看加入的课程。
- **课程详情**：显示课时列表、已上传资料。
- **资料上传**：支持拖拽上传、进度条、多文件批量上传（`multipart` 提交到 Route Handler）。
- **资料列表**：显示状态流转（uploaded → parsing → ready）；可用 **polling** 或 **SSE** 推送状态（B2 可选）。
- **资料预览**：对于支持的格式（PDF、图片），可在线预览。

### 决策 7：与 Python Worker 的通信协议

**Next.js 服务端**与 Python Worker 通过 Redis 队列通信，无需额外 RPC。

但为了监控与管理，建议预留 HTTP API（可选）：

```
Python Worker 暴露：
GET /health — 健康检查
GET /worker/status — Worker 状态（处理速度、队列深度等）
POST /worker/force-process/{task_id} — 手工重新处理某个失败任务
```

### 决策 8：资料的权限与可见性

- **课程资料**：仅加入该课程的学生可查询。
- **个人资料**：仅用户自己可查询。
- **跨课程**：学生不可跨课程查询资料。

权限检查在 Agent 侧实现（`knowledge_query` 中检查 course_id 与 user 是否匹配）。

## 实现边界与工程清单

本小节归纳 **B2 / Phase 7 交付边界**（与 Phase 8 B3 UI/采集区分）；实现与验收以 **上文决策 + 本节清单** 为准。

### 产品边界

- **Agent（教育平台会话）**：`knowledge_query` **课程腿** 命中 **平台侧 LightRAG 索引**（`LIGHTRAG_*` + 该课 `workspace`）；**个人腿** 使用 **本地 `rag_mvp` 个人库**。二者工具层 **并列**，由 `sources` 与 **runtime**（会话绑定的 `course_id`、用户身份）路由，**不由模型自填课程边界**（决策 5、H5）。
- **无 Agent**：直接使用 **`rag_mvp`** CLI 或 `engine.ingest_*` / `engine.query`；平台仅在「Agent + 绑定用户 + 课程上下文」路径下经 internal API 做 **鉴权与多租户数据面**。
- **单引擎**：课程与个人 **同一套 `rag_mvp` / `engine` 范式**；课程租户键为 **`workspace`（与 `course_id` 1:1）**，见决策 3。

### 技术要点（目标一览）

| 维度 | Phase 7 要求 |
|------|----------------|
| 库表 | LightRAG **`LIGHTRAG_*`**，`(workspace, id)`；平台 **`materials` 等业务表** |
| 写入 | Worker：**RAGAnything** + 绑定该课 **`workspace`** 的 **`LightRAG` ingest** |
| 课程查询 | **`knowledge_query` 课程腿** → **`LightRAG.query` / multimodal**（与 `engine` 一致） |
| 隔离 | **每课一 `workspace`**，共享 DDL，不按课拆表名 |

### 代码任务清单（可拆 PR）

1. **工厂**：`get_lightrag_for_course(course_id)`（或等价名），**固化 `course_id` → `workspace`**，复用 `engine._build_rag` 的初始化逻辑，避免复制粘贴。
2. **Worker**：`process_parse_and_index` **解析阶段**改为复用 **`engine.parse_file` 同源 API**（与 CLI `rag parse` 一致），**ingest 阶段**与 **`engine.ingest_*` / 文档完整管线** 同源；去除与上述分叉的独立解析实现；`indexed_chunk_count` 等可由引擎统计或约定回填规则。
3. **`knowledge_query`**：课程腿 **仅** 走引擎查询；internal API **鉴权**，不维护并行 SQL 检索 DSL。
4. **测试**：课程与个人 **hybrid / multimodal**；**跨课 `workspace` 隔离**（不得串数据）。

## 文件清单

### 新建 / 扩展文件（Next.js + Prisma，`edu-platform/`）

- [edu-platform/prisma/schema.prisma](file:///edu-platform/prisma/schema.prisma)（扩展）
  职责：新增 `Course`、`Lesson`、`CourseEnrollment`、`Material` 等模型；迁移由 **Prisma Migrate** 管理（替代 Flyway）。**课程向量与 LightRAG 文档块不在 Prisma 内建模**，由 **`LIGHTRAG_*`** 承担。

- [edu-platform/lib/services/courseService.ts](file:///edu-platform/lib/services/courseService.ts)
  职责：课程与选课业务逻辑。

- [edu-platform/lib/services/materialService.ts](file:///edu-platform/lib/services/materialService.ts)
  职责：资料上传、元数据写入、**Redis 任务入队**。

- [edu-platform/lib/minio.ts](file:///edu-platform/lib/minio.ts)
  职责：MinIO 客户端封装（上传、预签名 URL、删除）。

- [edu-platform/lib/redis.ts](file:///edu-platform/lib/redis.ts)
  职责：Redis 连接（Stream、bind 等复用）。

- [edu-platform/lib/queue/ragTask.ts](file:///edu-platform/lib/queue/ragTask.ts)
  职责：向 **`RAG_TASK_STREAM_NAME`**（默认 `edu:rag:tasks:stream`）执行 **`XADD`**，载荷仅 `material_id` + `operation`。

- [edu-platform/app/api/v1/courses/route.ts](file:///edu-platform/app/api/v1/courses/route.ts)
  职责：`POST /api/v1/courses` 等课程集合端点（可按需拆分为 `[courseId]/route.ts`）。

- [edu-platform/app/api/v1/courses/[courseId]/materials/route.ts](file:///edu-platform/app/api/v1/courses/[courseId]/materials/route.ts)
  职责：资料 `POST`（multipart）、`GET` 列表。

- [edu-platform/app/api/v1/materials/[materialId]/route.ts](file:///edu-platform/app/api/v1/materials/[materialId]/route.ts)
  职责：`DELETE /api/v1/materials/{material_id}`。

- [edu-platform/app/(app)/courses/page.tsx](file:///edu-platform/app/(app)/courses/page.tsx)
  职责：课程列表页。

- [edu-platform/app/(app)/courses/[courseId]/page.tsx](file:///edu-platform/app/(app)/courses/[courseId]/page.tsx)
  职责：课程详情、资料列表与上传入口。

- [edu-platform/components/MaterialUpload.tsx](file:///edu-platform/components/MaterialUpload.tsx)
  职责：资料上传 Client 组件（拖拽、进度）。

### 新建文件（Python Worker）

- [src/rag_mvp/worker.py](file:///src/rag_mvp/worker.py)
  职责：Redis **Stream** consumer（`XREADGROUP` / `XACK` / `XAUTOCLAIM`）。
  功能：
  - `XGROUP CREATE`（幂等）+ 消费组处理
  - 从 DB 加载 `materials` 行（不信任 Stream 业务字段）
  - 调用 `process_parse_and_index` / `process_delete_material`
  - 处理失败写 `FAILED` 后 **ACK**；崩溃未 ACK 的消息由 `XAUTOCLAIM` 回收

- [src/rag_mvp/material_processor.py](file:///src/rag_mvp/material_processor.py)
  职责：资料处理核心逻辑（MinIO 下载、状态机、**调用与 `engine.parse_file` / ingest 同源的 RAGAnything**）。
  功能：
  - 解析：**委托或内联复用** `engine` 中与 **`rag parse` 相同的解析路径**（见决策 2、决策 3「解析与 Ingest」），**不**保留与 `parse_file` 并行的仅文本抽取主路径。
  - `ingest_material_to_lightrag(course_id, material_id, …)` — **绑定 `workspace` 的 `LightRAG` ingest**，写入 **`LIGHTRAG_*`**
  - `update_material_status(...)` — 更新 `materials` 状态与计数

> 页面与 **`lib/services/*`** 的分工：复杂查询可在 Server Component 内直接调 `lib/services`；浏览器侧仅 `fetch('/api/v1/...')`，避免重复实现。

## 接口契约

### 1. POST /api/v1/courses (创建课程)

**请求**：
```json
{
  "name": "Python 基础",
  "description": "学习 Python 编程基础",
  "cover_image_url": "..."
}
```

**响应**（HTTP 201）：
```json
{
  "id": "course_uuid",
  "name": "Python 基础",
  "status": "DRAFT",
  "created_at": "..."
}
```

### 2. POST /api/v1/courses/{course_id}/materials (上传资料)

**请求**（multipart/form-data）：
```
POST /api/v1/courses/{course_id}/materials
Content-Type: multipart/form-data

file: <binary>
lesson_id: <optional>
```

**响应**（HTTP 201）：
```json
{
  "id": "material_uuid",
  "original_filename": "lecture.pdf",
  "status": "UPLOADED",
  "created_at": "..."
}
```

### 3. GET /api/v1/courses/{course_id}/materials (列表查询)

**查询参数**：
- `status`: UPLOADED | PARSING | READY | FAILED（可选）

**响应**：
```json
{
  "materials": [
    {
      "id": "material_uuid",
      "filename": "lecture.pdf",
      "file_type": "pdf",
      "status": "READY",
      "indexed_chunk_count": 42,
      "created_at": "..."
    }
  ]
}
```

### 4. DELETE /api/v1/materials/{material_id} (删除资料)

从 MinIO、**`materials` 记录**及 **LightRAG 中该资料对应文档/块**（在正确 **`workspace`** 下）删除；具体 API 与引擎版本对齐。

### 5. GET /api/v1/courses/{course_id}/join (学生加入课程)

学生通过课程邀请码或教师添加加入课程。

> 调整为 `POST /api/v1/courses/{course_id}/join`。加入课程属于状态变更操作，不使用 GET。

### 6. Redis 队列消息格式

与 **决策 4** 一致，载荷 **仅** `material_id` + `operation`（及 `task_id` / `created_at` 等元信息）；`course_id`、路径、类型由 Worker **`SELECT materials`** 加载。

```json
{
  "task_id": "task_uuid",
  "material_id": "material_uuid",
  "operation": "parse_and_index",
  "created_at": "2026-05-08T09:00:00Z"
}
```

## 实施顺序

### Next.js（Prisma + API + 页面）

1. 扩展 `schema.prisma`，**`prisma migrate`** 建课程、资料、选课表。
2. 实现 `lib/services/courseService.ts`、`materialService.ts` 与 MinIO、Redis 封装。
3. 实现 **`app/api/v1/courses/**`、`materials/**`** Route Handlers（含权限：教师/学生/选课）。
4. Vitest/Jest 覆盖服务层与 API；必要时 **Playwright** 做上传 E2E。

### Next.js 页面

1. 课程列表与课程详情（资料列表、上传）。
2. 资料状态展示（轮询或 SSE）。
3. 学生加入课程（`POST .../join`）UI。

### Python Worker

1. 实现 `worker.py`，连接 Redis 与 PostgreSQL。
2. 实现 `material_processor.py`，集成 PDF 解析、**RAG-Anything + LightRAG ingest**（`LIGHTRAG_*` + 该课 `workspace`）。
3. 处理各种文件格式（PDF、PPTX、Docx 等）。
4. 错误处理与重试机制。
5. 监控与日志。

### 集成

1. Next.js 与 MinIO 集成测试（上传与预签名 URL）。
2. Next.js 与 Redis 入队集成测试。
3. Python Worker 与 PostgreSQL + LightRAG 集成测试。
4. E2E：教师上传 PDF → Worker 处理 → Agent 查询 → 返回正确结果。

## 注意事项

### 1. PostgreSQL 表管理

- **业务表**：`courses`、`materials` 等由 **Prisma Migrate** 管理。
- **向量与 RAG 元数据**：**`LIGHTRAG_*`** 由 **LightRAG**（PGKV / PGVector / PGDocStatus / 可选 PGGraph）初始化与演进；租户隔离靠 **`workspace`**，与 **`course_id` 的映射在应用层唯一实现**（见决策 3）。
- **禁止**再为每门课动态 `CREATE TABLE course_*_chunks` 或并行维护一套手写 chunk 表作为主检索源。

### 2. 大文件上传

若支持大文件（如 1 GB PPT），上传可能超时。建议：

- 前端分块上传（多个小块并行）。
- **Route Handler** 侧合并分块或协调 MinIO multipart。
- 或使用 MinIO 的预签名 URL，**浏览器直传** MinIO，完成后回调平台写入 `Material` 并入队。

### 3. RAG 处理耗时

PDF 解析 + embedding + ingest 可能需要几十秒到几分钟（取决于文件大小与模型）。建议：

- Worker 处理时间长（> 10s）时定期更新 Material status（如 `parsing: 50%`）。
- 前端定期 polling 或 WebSocket 长连接接收进度更新。

### 4. 课程 RAG 的删除与清理

删除课程或资料时：同步清理 **`materials` / MinIO** 与 **`LIGHTRAG_*`** 中该 **`workspace`**（及资料对应文档）的数据；调用 **LightRAG 或存储层提供的删除能力**，**不得**误删其他 `workspace`；与 `courses` / `materials` 软删策略对齐。

### 5. 个人 RAG 与课程 RAG 的混用

个人库与课程库在 Agent 内 **双源并列**（决策 5）；二者在实现上均归 **`rag_mvp` / `LightRAG` 引擎范式**：个人用默认或独立 `workspace` / 存储配置，课程用 **`course_id` 派生的 `workspace`**（决策 3）。个人侧可保留 Json 等便于本机开发的配置，课程侧以 **PG `LIGHTRAG_*`** 为 Phase 7 交付要求。

## 验收标准

### 课程管理

- 教师可创建、编辑、删除课程。
- 学生可加入课程，查看课程资料。
- 课程被发布后学生才能加入。

### 资料上传

- 支持上传 **PDF、Markdown、TXT**（与 Worker `parse_material` 一致）。
- 上传后资料状态为 UPLOADED。
- Worker 自动处理，状态流转至 READY。
- 处理失败时状态为 FAILED，记录错误信息。

### RAG 索引

- 资料准备好后 (status=READY)，`indexed_chunk_count > 0`（或由 LightRAG 统计回填的等价指标）。
- 在 **`LIGHTRAG_*`** 中，该资料在对应课的 **`workspace`** 下可查（文档块 / 向量行由 LightRAG 管理）。

### Agent 查询

- Agent 在 **session 已绑定课程** 时调用 `knowledge_query`（`sources=all` 或 `course`）；**`course_id` 仅来自 `TurnRuntimeContext`**，**不**作为工具参数传入（见决策 5）。
- 课程腿仅通过 **绑定该课 `workspace` 的 `LightRAG.query` / multimodal**（与 `engine` 个人路径同范式）；**无**手写 SQL 向量主路径。
- 返回结果带 `origin="course"` 标注（与个人腿并列）。
- 不同课程的查询结果隔离。

### 权限

- 学生只能查看自己加入的课程资料。
- 跨课程查询不返回其他课程内容。

## 本阶段不做

- 不做资料分享与公开课程（所有资料默认课程内部可见）。
- 不做复杂的文件预览（如 PPT 逐页预览）。
- 不做文件版本历史管理。
- 不做病毒扫描与安全检查。
- 不做智能标签与自动分类。

## 确认的开放点

### 1. RAG ingest 的模型选择

课程向量由 **LightRAG 配置的 embedding** 写入 **`LIGHTRAG_*`**（与 **RAG-Anything + `LightRAG` ingest** 一致）；**共享表 + 每课 `workspace`（由 `course_id` 稳定派生）**（决策 3）。

是否使用本地 embedding 模型（如 BGE）以降低成本，还是保持用云 embedding？

> 已确认：默认使用 Ollama `bge-m3` 作为 embedding 模型；同时通过配置项支持切换任意 embedding provider/model（与 A1 的 provider 配置思路一致）。

### 2. 是否支持资料编辑

上传后的资料是否允许编辑或重新索引（如发现解析错误）？

> 初期不支持编辑。若需修改，删除后重新上传。后期可加"重新索引"功能。

### 3. Lesson 与 Material 的关系

当前方案中 Material 关联 lesson_id（可选）。是否需要更强的关联，如"必须属于某个 lesson"？

> 关联为可选，允许课程级别的共享资料（不属于特定课时）。
