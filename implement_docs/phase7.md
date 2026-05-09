# Phase 7：B2 课程资料与 RAG 知识库详细方案

## 目标与背景

B1 完成后，教育平台已经具备用户身份管理与 Agent 绑定能力。B2 的核心目标是建立**课程资料管理与 RAG 知识库系统**，使教育平台能够：

1. **课程与课时管理**：创建课程、编辑课程信息、学生加入、课时 CRUD。
2. **资料上传与处理**：支持多格式（PDF、PPTX、Word、Markdown、TXT、图片）上传，异步处理转换与 RAG 索引。
3. **课程 RAG 存储**：每个课程对应 PostgreSQL 中独立的 LightRAG namespace，课程间知识图谱完全隔离。
4. **个人 RAG 与课程 RAG 的路由**：Agent 侧的 `knowledge_query` 工具根据 session context 路由到正确来源。
5. **资料处理流水线**：Java 后端创建任务 → Redis 队列 → Python Worker 处理 → 回写状态。

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
1. 前端上传文件 → Java 后端接收
2. 存储原始文件到 MinIO
3. 创建 Material record（status=UPLOADED）
4. 生成处理任务，推入 Redis 队列
5. Python Worker（rag_mvp）消费任务：
   - 从 MinIO 下载文件
   - 执行 parse（PDF → text、PPTX → text + images 等）
   - 更新 Material record（status=PARSED）
   - 执行 RAG ingest（调用 LightRAG）
   - 更新 Material record（status=READY, indexed_chunk_count）
6. 处理失败时更新 status=FAILED，记录错误信息
7. Agent 可查询 READY 状态的资料
```

### 决策 3：课程 RAG 的 PostgreSQL 存储方案

每个课程使用独立的 namespace（表前缀）：

```sql
-- course_{course_id}_chunks
CREATE TABLE course_<uuid>_chunks (
    id UUID PRIMARY KEY,
    material_id UUID NOT NULL,  -- 来自哪个资料
    chunk_text TEXT NOT NULL,
    chunk_index INT,
    embedding vector(1536),  -- pgvector
    metadata JSONB,  -- {page_number, section, ...}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- course_{course_id}_entities
CREATE TABLE course_<uuid>_entities (
    id UUID PRIMARY KEY,
    entity_name VARCHAR(255),
    entity_type VARCHAR(50),  -- "PERSON", "CONCEPT", "LOCATION", ...
    description TEXT,
    embedding vector(1536),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- course_{course_id}_relationships
CREATE TABLE course_<uuid>_relationships (
    id UUID PRIMARY KEY,
    source_entity_id UUID REFERENCES course_<uuid>_entities(id),
    target_entity_id UUID REFERENCES course_<uuid>_entities(id),
    relationship_type VARCHAR(100),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**好处**：

- 完全隔离，跨课程查询天生失效。
- LightRAG 支持独立 namespace，无需修改库。
- 后期删除课程时可直接删除对应表。

**成本**：

- 表数量多（3 × N 课程数）。
- 表管理复杂（自动建表、清理）。

**替代方案**（不采用）：

- 单表 + `course_id` 列：跨课程查询风险高，误删数据风险高。
- 不同数据库：运维成本高，查询跨数据库复杂。

### 决策 4：资料处理任务的格式与队列

Redis 队列中的任务格式：

```json
{
  "task_id": "task_uuid",
  "course_id": "course_uuid",
  "material_id": "material_uuid",
  "operation": "parse_and_index",
  "file_type": "pdf",
  "minio_path": "materials/course_xxx/filename.pdf",
  "created_at": "2026-05-08T09:00:00Z"
}
```

队列名：`edu:rag:tasks:pending`。

Python Worker 定期（如每 5 秒）从队列中取任务，处理后更新 Material record 的 status。

### 决策 5：Agent 侧的多源 RAG 查询

Agent 的 `knowledge_query` 工具改进（对标 A4）：

```python
async def knowledge_query(
    query: str,
    sources: Literal["personal", "course", "all"] = "all",
    course_id: str | None = None,
    top_k: int = 5,
) -> list[QueryResult]:
    """
    当 session context 包含 course_id 时：
    - sources="all" (默认)：先查课程 RAG，后查个人 RAG，合并结果
    - sources="course"：仅查课程 RAG
    - sources="personal"：仅查个人 RAG
    
    当 session context 无 course_id 时：
    - 仅查个人 RAG（无法查课程 RAG）
    """
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

### 决策 6：前端课程管理与资料上传

前端页面：

- **课程列表**：教师查看自己的课程 / 学生查看加入的课程。
- **课程详情**：显示课时列表、已上传资料。
- **资料上传**：支持拖拽上传、进度条、多文件批量上传。
- **资料列表**：显示状态流转（uploaded → parsing → ready）。
- **资料预览**：对于支持的格式（PDF、图片），可在线预览。

### 决策 7：与 Python Worker 的通信协议

Java 后端与 Python Worker 通过 Redis 队列通信，无需额外 RPC。

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

## 文件清单

### 新建文件（Java 后端）

- [edu-platform/src/main/java/com/eduagent/entity/Course.java](file:///edu-platform/src/main/java/com/eduagent/entity/Course.java)
  职责：JPA Course 实体。

- [edu-platform/src/main/java/com/eduagent/entity/Lesson.java](file:///edu-platform/src/main/java/com/eduagent/entity/Lesson.java)
  职责：JPA Lesson 实体。

- [edu-platform/src/main/java/com/eduagent/entity/CourseEnrollment.java](file:///edu-platform/src/main/java/com/eduagent/entity/CourseEnrollment.java)
  职责：JPA CourseEnrollment 实体。

- [edu-platform/src/main/java/com/eduagent/entity/Material.java](file:///edu-platform/src/main/java/com/eduagent/entity/Material.java)
  职责：JPA Material 实体。

- [edu-platform/src/main/java/com/eduagent/repository/CourseRepository.java](file:///edu-platform/src/main/java/com/eduagent/repository/CourseRepository.java)
  职责：Course 数据访问接口。

- [edu-platform/src/main/java/com/eduagent/repository/LessonRepository.java](file:///edu-platform/src/main/java/com/eduagent/repository/LessonRepository.java)
  职责：Lesson 数据访问接口。

- [edu-platform/src/main/java/com/eduagent/repository/MaterialRepository.java](file:///edu-platform/src/main/java/com/eduagent/repository/MaterialRepository.java)
  职责：Material 数据访问接口。

- [edu-platform/src/main/java/com/eduagent/dto/CourseRequest.java](file:///edu-platform/src/main/java/com/eduagent/dto/CourseRequest.java)
  职责：课程创建/更新请求 DTO。

- [edu-platform/src/main/java/com/eduagent/dto/CourseResponse.java](file:///edu-platform/src/main/java/com/eduagent/dto/CourseResponse.java)
  职责：课程响应 DTO。

- [edu-platform/src/main/java/com/eduagent/service/CourseService.java](file:///edu-platform/src/main/java/com/eduagent/service/CourseService.java)
  职责：课程业务逻辑。

- [edu-platform/src/main/java/com/eduagent/service/MaterialService.java](file:///edu-platform/src/main/java/com/eduagent/service/MaterialService.java)
  职责：资料业务逻辑，包含上传、处理任务队列操作。

- [edu-platform/src/main/java/com/eduagent/service/MinIOService.java](file:///edu-platform/src/main/java/com/eduagent/service/MinIOService.java)
  职责：MinIO 操作（上传、下载、删除）。

- [edu-platform/src/main/java/com/eduagent/service/RagTaskQueueService.java](file:///edu-platform/src/main/java/com/eduagent/service/RagTaskQueueService.java)
  职责：Redis 队列操作（任务入队、监控）。

- [edu-platform/src/main/java/com/eduagent/controller/CourseController.java](file:///edu-platform/src/main/java/com/eduagent/controller/CourseController.java)
  职责：提供课程 CRUD 端点。

- [edu-platform/src/main/java/com/eduagent/controller/MaterialController.java](file:///edu-platform/src/main/java/com/eduagent/controller/MaterialController.java)
  职责：提供资料上传、查询、删除端点。

- [edu-platform/src/main/resources/db/migration/V2__courses_materials.sql](file:///edu-platform/src/main/resources/db/migration/V2__courses_materials.sql)
  职责：Flyway 迁移脚本，建课程和资料相关的表。

### 新建文件（Python Worker）

- [src/rag_mvp/worker.py](file:///src/rag_mvp/worker.py)
  职责：Redis 队列 consumer，处理资料 parse & index 任务。
  功能：
  - 连接 Redis，监听 `edu:rag:tasks:pending`
  - 取任务，调用 parse_and_index()
  - 更新 Material record 的 status
  - 异常处理与重试

- [src/rag_mvp/material_processor.py](file:///src/rag_mvp/material_processor.py)
  职责：资料处理核心逻辑。
  功能：
  - `parse_material(file_type, file_path)` — 解析各种格式
  - `ingest_to_course_rag(course_id, chunks)` — 索引到 PostgreSQL
  - `update_material_status(material_id, status, metadata)` — 更新状态

### 新建文件（React 前端）

- [edu-platform-web/src/pages/Courses/index.tsx](file:///edu-platform-web/src/pages/Courses/index.tsx)
  职责：课程列表页面。

- [edu-platform-web/src/pages/CourseDetail/index.tsx](file:///edu-platform-web/src/pages/CourseDetail/index.tsx)
  职责：课程详情页面，包含资料列表、上传功能。

- [edu-platform-web/src/pages/Materials/index.tsx](file:///edu-platform-web/src/pages/Materials/index.tsx)
  职责：资料管理页面。

- [edu-platform-web/src/components/MaterialUpload.tsx](file:///edu-platform-web/src/components/MaterialUpload.tsx)
  职责：资料上传组件，支持拖拽、进度条。

- [edu-platform-web/src/services/courseService.ts](file:///edu-platform-web/src/services/courseService.ts)
  职责：课程相关 API 调用。

- [edu-platform-web/src/services/materialService.ts](file:///edu-platform-web/src/services/materialService.ts)
  职责：资料相关 API 调用。

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

从 MinIO 与 PostgreSQL 中删除该资料及其 chunks。

### 5. GET /api/v1/courses/{course_id}/join (学生加入课程)

学生通过课程邀请码或教师添加加入课程。

> 调整为 `POST /api/v1/courses/{course_id}/join`。加入课程属于状态变更操作，不使用 GET。

### 6. Redis 队列消息格式

```json
{
  "task_id": "task_uuid",
  "course_id": "course_uuid",
  "material_id": "material_uuid",
  "operation": "parse_and_index",
  "file_type": "pdf",
  "minio_path": "materials/course_xxx/filename.pdf",
  "created_at": "2026-05-08T09:00:00Z"
}
```

## 实施顺序

### 后端（Java）

1. 新增数据库迁移脚本，建课程、资料、选课表。
2. 实现 JPA 实体与 Repository。
3. 实现 CourseService、MaterialService、MinIOService、RagTaskQueueService。
4. 实现 CourseController、MaterialController。
5. 单元与集成测试。

### 前端（React）

1. 课程列表页面。
2. 课程详情页面。
3. 资料上传组件与状态展示。
4. 学生加入课程功能。

### Python Worker

1. 实现 `worker.py`，连接 Redis 与 PostgreSQL。
2. 实现 `material_processor.py`，集成 PDF 解析、LightRAG ingest。
3. 处理各种文件格式（PDF、PPTX、Docx 等）。
4. 错误处理与重试机制。
5. 监控与日志。

### 集成

1. Java 后端与 MinIO 集成测试。
2. Java 后端与 Redis 队列集成测试。
3. Python Worker 与 PostgreSQL + LightRAG 集成测试。
4. E2E：教师上传 PDF → Worker 处理 → Agent 查询 → 返回正确结果。

## 注意事项

### 1. PostgreSQL 表管理

自动建表的问题：LightRAG 创建的表结构可能不完全符合需求。建议：

- 让 Python Worker 在索引前检查表是否存在。
- 若不存在，调用 Flyway/Liquibase 创建（Java 侧）。
- 或者 Worker 侧自己执行 CREATE TABLE IF NOT EXISTS。

### 2. 大文件上传

若支持大文件（如 1 GB PPT），上传可能超时。建议：

- 前端分块上传（多个小块并行）。
- Java 后端支持分块合并。
- 或使用 MinIO 的预签名 URL，直接上传到 MinIO。

### 3. RAG 处理耗时

PDF 解析 + embedding + ingest 可能需要几十秒到几分钟（取决于文件大小与模型）。建议：

- Worker 处理时间长（> 10s）时定期更新 Material status（如 `parsing: 50%`）。
- 前端定期 polling 或 WebSocket 长连接接收进度更新。

### 4. 课程 RAG 的删除与清理

删除课程时需要删除对应的所有 PostgreSQL 表。建议：

- 执行 DROP TABLE course_<uuid>_*；
- 或标记为"已删除"而不物理删除（软删除）。

### 5. 个人 RAG 与课程 RAG 的混用

当前 Agent 侧有个人 RAG（本地 JSON/JSONL）与课程 RAG（PostgreSQL）两套。数据来源与存储完全分离，增加复杂度。

简化方案（不采用）：全部用 PostgreSQL。缺点是本地开发与测试不便（需要 PostgreSQL 与 pgvector）。

## 验收标准

### 课程管理

- 教师可创建、编辑、删除课程。
- 学生可加入课程，查看课程资料。
- 课程被发布后学生才能加入。

### 资料上传

- 支持上传 PDF、PPTX、Word、Markdown 等格式。
- 上传后资料状态为 UPLOADED。
- Worker 自动处理，状态流转至 READY。
- 处理失败时状态为 FAILED，记录错误信息。

### RAG 索引

- 资料准备好后 (status=READY)，indexed_chunk_count > 0。
- PostgreSQL 中能查到对应 course_xxx_chunks 表的数据。

### Agent 查询

- Agent 在有 course_id 的 session 中调用 `knowledge_query`，优先查课程 RAG。
- 返回结果带 `origin="course"` 标注。
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

当前方案使用 LightRAG 与 embedding 模型（如 OpenAI ada）生成 vectors 存入 pgvector。

是否使用本地 embedding 模型（如 BGE）以降低成本，还是保持用云 embedding？

> 已确认：默认使用 Ollama `bge-m3` 作为 embedding 模型；同时通过配置项支持切换任意 embedding provider/model（与 A1 的 provider 配置思路一致）。

### 2. 是否支持资料编辑

上传后的资料是否允许编辑或重新索引（如发现解析错误）？

> 初期不支持编辑。若需修改，删除后重新上传。后期可加"重新索引"功能。

### 3. Lesson 与 Material 的关系

当前方案中 Material 关联 lesson_id（可选）。是否需要更强的关联，如"必须属于某个 lesson"？

> 关联为可选，允许课程级别的共享资料（不属于特定课时）。
