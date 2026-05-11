# 教育平台（EduAgent Phase 6 / B1 + Phase 7 / B2 + Phase 8 / B3）

与本文同级目录为 **Next.js 工程根**：`app/`、`lib/`、`prisma/`、`middleware.ts` 等均与 `README.md` 同级，无嵌套子包。

**Phase 7（B2）**：课程与课时、学生选课、资料上传（MinIO + multipart）、Redis **Stream** 任务队列、Python Worker 处理资料。Worker **只信任队列里的 `material_id`**，其余字段 **`SELECT materials`**；解析与 CLI `rag parse` 同源（**`engine.parse_file`**），索引阶段将 MinerU 产物经 **`insert_content_list`** 写入同一 PostgreSQL 库中的 **`LIGHTRAG_*`**，按每门课 **`workspace`**（`course_<小写 course_id>`，与 `course_id` 1:1）隔离。Agent 侧 **`knowledge_query` 须显式传 `sources`**（`personal` | `course` | `all`）；`course_id` **仅**来自会话 runtime；课程命中前调 **`GET /api/v1/internal/course-rag-access`** 做选课鉴权，检索本身在 **`rag_mvp`** 内走 **`LightRAG.aquery_data`**（与个人腿同一数据面 API）。详细契约见 [`implement_docs/phase7.md`](../implement_docs/phase7.md)；与实现对齐的审计摘要见 [`review_docs/review_phase7.md`](../review_docs/review_phase7.md)。

**Phase 8（B3）**：课程页 **HTTP SSE 聊天**（平台将 EduAgent 的 OpenAI 兼容 SSE **规范化为** `text` / `citation` / `done` 事件）、**`qa_logs`** 与 **`course_chat_sessions`**（每用户每课一条 Agent `session_id`）、学生 **采集开关**（`qa_collection_enabled`；关闭后仍应答但不写库）、教师 **课程聚合统计**、学生 **学习进度**、**GDPR** 导出/软删。平台通过 **`EDU_AGENT_BASE_URL`** 调用 **`POST /v1/sessions`** 与 **`POST /v1/chat/completions`**，请求头注入 **`X-Platform-Course-Id` / `X-Platform-Lesson-Id`**（`X-Platform-User-Id` 与 query `user_id` 一致时为 **Agent user id**）。学生使用聊天前 JWT 须含已绑定的 **`agent_user_id`**。契约见 [`implement_docs/phase8.md`](../implement_docs/phase8.md)；审计摘要见 [`review_docs/review_phase8.md`](../review_docs/review_phase8.md)。

## 技术栈

- Next.js App Router（Route Handlers：`app/api/v1/**/route.ts`）
- TypeScript、Prisma、PostgreSQL
- **Redis**（绑定 challenge、绑定限流；**Phase 7**：RAG 任务 **Stream** `edu:rag:tasks:stream` 等，见环境变量）
- **MinIO**（S3 兼容，Phase 7 资料对象存储）
- **Python Worker**（仓库根 `src/rag_mvp/worker.py`，入口脚本 `edu-rag-worker`）
- JWT（`jose`）、密码 argon2id、凭证码 HMAC-SHA256（带 pepper）
- Ant Design（`@ant-design/nextjs-registry`）

## 本地开发

### 用 Docker 启动 PostgreSQL、Redis 与 MinIO（可选）

[`docker-compose.yml`](docker-compose.yml) 提供：

- **PostgreSQL（pgvector 镜像）**：`edu` / `edu` / `edu_platform`，端口 `5432`（Prisma 管理业务表 `courses` / `materials` 等；**LightRAG** 在同一库中维护 **`LIGHTRAG_*`** 向量与文档元数据，租户键为 **`workspace`**）
- **Redis**：端口 `6379`
- **MinIO**：API `9000`，控制台 `9001`；默认 root 用户/密码见 compose 文件中的 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`

```bash
docker compose up -d
```

在 **MinIO 控制台**（<http://localhost:9001>）为 `.env` 中的 `MINIO_BUCKET` **手动创建同名 Bucket**（例如 `edu-materials`），否则资料上传会失败。

在 `.env` 中至少设置：

```env
DATABASE_URL="postgresql://edu:edu@localhost:5432/edu_platform?schema=public"
REDIS_URL="redis://localhost:6379"
MINIO_ENDPOINT="http://127.0.0.1:9000"
MINIO_ACCESS_KEY=minio
MINIO_SECRET_KEY=minio_minio_minio
MINIO_BUCKET=edu-materials
MINIO_USE_SSL=false
```

停止并删除容器（保留数据卷）：`docker compose down`；连数据一并删：`docker compose down -v`。

若本机已有占用 `5432` 的 Postgres，可改 `docker-compose.yml` 里 `ports` 为 `"5433:5432"`，并把 `DATABASE_URL` 里的端口改成 `5433`。

**从旧版「纯 postgres:16」卷升级**：若需 pgvector，请改用 compose 中的镜像并**重建数据卷**或自行在实例上安装 `vector` 扩展，否则 LightRAG 的 PG 向量存储无法初始化。

### 配置 `JWT_SECRET`、`CREDENTIAL_CODE_PEPPER`、`BIND_CREDENTIAL_API_KEY`

三者均为**仅服务端可见**的机密，写入 `edu-platform/.env`（不要提交到 Git）。代码要求：

| 变量 | 用途 | 最低长度（当前实现） |
|------|------|----------------------|
| `JWT_SECRET` | 签发/校验用户 access JWT、refresh 的 HMAC | ≥ 16（建议 ≥ 32 随机字节） |
| `CREDENTIAL_CODE_PEPPER` | 对 8 位凭证码做 HMAC-SHA256 的 pepper | ≥ 16 |
| `BIND_CREDENTIAL_API_KEY` | Agent 调用绑定接口时与请求头比对 | ≥ 16 |

**生成随机值（任选一种）：**

PowerShell（Windows）：

```powershell
[Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Maximum 256 }))
```

重复执行三次，分别填入三个变量；或 OpenSSL：

```bash
openssl rand -base64 48
```

Node（在 `edu-platform` 目录）：

```bash
node -e "console.log(require('crypto').randomBytes(48).toString('base64url'))"
```

把输出**原样**放进 `.env` 的引号内即可，例如：

```env
JWT_SECRET="paste-one-random-string-here"
CREDENTIAL_CODE_PEPPER="paste-another-different-string"
BIND_CREDENTIAL_API_KEY="paste-third-string-for-agent-only"
```

**注意：**三者应**互不相同**。更换 `JWT_SECRET` 会使已签发的 access JWT 全部失效；更换 `CREDENTIAL_CODE_PEPPER` 会使库里已有凭证码哈希全部无法匹配（等于作废旧凭证）；更换 `BIND_CREDENTIAL_API_KEY` 后需在 Agent 侧同步更新请求头 `X-Platform-Bind-Key`。

### Phase 7：资料存储、队列、内部鉴权与 Agent

| 变量 | 用途 |
|------|------|
| `MINIO_*` | 资料对象存储（上传、删除）；缺省则资料相关 API 返回 503 |
| `INTERNAL_API_KEY` | 服务端内部接口（≥16 字符）；未配置则 `/api/v1/internal/course-rag-access` 返回 503 |
| `RAG_TASK_STREAM_NAME` | 可选，默认 `edu:rag:tasks:stream`（Next `XADD` 与 Python Worker 一致） |
| `RAG_TASK_STREAM_GROUP` | 可选，默认 `edu-rag-workers` |
| `RAG_TASK_CONSUMER_NAME` | 可选，区分多 Worker 进程 |
| `RAG_STREAM_CLAIM_IDLE_MS` | 可选，`XAUTOCLAIM` 最小空闲毫秒（默认 300000） |
| `RAG_MATERIAL_STALE_SEC` | 可选，资料卡在 `PARSING` / `PARSED` / `INDEXING` 超过该秒数可被 Worker 再次抢占（默认 1800） |
| `MATERIAL_MAX_UPLOAD_BYTES` | 可选，单文件上限（字节），默认约 50MiB |

**Agent（edu_agent）** 查询课程 RAG 时需与平台一致：

- `EDU_PLATFORM_BASE_URL`：例如 `http://127.0.0.1:3000`
- `EDU_PLATFORM_INTERNAL_API_KEY`：与 **`INTERNAL_API_KEY` 相同**
- Agent 进程还需能连同一 **`DATABASE_URL`**（与 Worker 相同：LightRAG 通过 `DATABASE_URL` 推导 **`POSTGRES_*`** 连接 PG；`knowledge_query` 可选地用其查询 `materials.original_filename` 填充返回中的 **`material_title`**）

嵌入模型与维度由 **仓库根 `src/rag_mvp/config.py` / `.env`** 与 `rag_mvp.llm.build_embedding_func`（默认 **Ollama** `bge-m3`，1024 维）统一配置（与 CLI 个人库一致）；课程与个人共用同一套嵌入，课程向量写入 **`LIGHTRAG_*`**（`workspace` 隔离）。

### Phase 8（B3）：EduAgent HTTP 网关与平台环境变量

本平台的 **课程聊天**、**创建 Agent 会话** 均通过 **服务端 `fetch`** 访问 Python **EduAgent HTTP API**（与 Phase 7 的「Agent 调平台内部接口」方向相反：此处是 **Next → Agent**）。

| 变量 | 用途 |
|------|------|
| **`EDU_AGENT_BASE_URL`** | **必填**（启用聊天时）：EduAgent 网关根 URL，**无**末尾 `/`。例如本地 `http://127.0.0.1:8765`。代码见 `lib/config.ts` → `getEduAgentBaseUrl()`，调用见 `lib/agentClient.ts`（`…/v1/sessions`、`…/v1/chat/completions`）。**EduAgent 进程不读取此变量**；它只监听 `edu_agent.yaml` 的 `runtime.gateway.host` / `port`（README 默认示例常为 `8765`）。 |
| **`EDU_AGENT_API_KEY`** | **可选**：若 Agent 启用了 HTTP 鉴权，则须与 Agent 侧密钥一致。平台以 **`Authorization: Bearer <值>`** 发送。Agent 侧：`edu-gateway` 启动时优先使用 **`edu_agent.yaml` 的 `runtime.gateway.api_key`**；若为空则回退环境变量 **`EDU_AGENT_API_KEY`**（见仓库根 `src/edu_agent/auth/checker.py`）。本地开发若 `require_http_key: false`，可不设本变量。 |

**本地典型顺序**：

1. **仓库根**启动网关：`uv run edu-gateway`（或 `--host 127.0.0.1 --port 8765`），保证与 `EDU_AGENT_BASE_URL` 一致。  
2. **`edu-platform/.env`**：`EDU_AGENT_BASE_URL=http://127.0.0.1:8765`；按需 `EDU_AGENT_API_KEY=…`（与 yaml 或 Agent 环境一致）。  
3. 学生/教师已在平台完成 **Agent 绑定**（JWT 中带 `agent_user_id`），否则聊天接口返回 **`AGENT_NOT_BOUND`**。  
4. 执行 **`npx prisma migrate deploy`**（或 `npm run db:migrate`），确保含 **Phase 8** 迁移（`qa_logs`、`course_chat_sessions`、用户采集字段等）。

**勿混淆**：`EDU_AGENT_BASE_URL` 指向 **本仓库 EduAgent 网关**；大模型厂商地址仍在仓库根 **`LLM_BASE_URL` / `edu_agent.yaml` 的 `providers.*.base_url`**。平台调课程 RAG 仍使用 **`EDU_PLATFORM_BASE_URL` + `EDU_PLATFORM_INTERNAL_API_KEY`**（Phase 7），与 `EDU_AGENT_*` 独立。

### RAG Worker（异步解析与索引）

在**仓库根目录**（含 `pyproject.toml`）安装依赖后启动（需已配置 `DATABASE_URL`、`REDIS_URL`、MinIO、可访问的 Ollama）：

```bash
uv sync
edu-rag-worker
```

Worker 使用 Redis **Stream**（`XREADGROUP` / `XACK` / `XAUTOCLAIM`）取任务；任务体仅含 **`task_id` / `material_id` / `operation` / `created_at`**，**不含** `course_id` 或 MinIO 路径；业务字段由 Worker **回查 `materials`**。解析与索引管线见仓库根 **`src/rag_mvp/material_processor.py`**、工厂与查询入口 **`src/rag_mvp/engine.py`**。详见 `implement_docs/phase7.md`。

### 客户端 IP 与网关

默认 **`TRUST_PROXY_HOPS=0`**：`getClientIp` **不**采用 `X-Forwarded-For` 左端（防伪造），优先 `X-Real-IP`。请在反向代理上为可信客户端 IP 注入 **`X-Real-IP`**，或在明确可信跳数时设置 `TRUST_PROXY_HOPS`（见 `.env.example`）。

### 应用启动步骤（在 `edu-platform` 目录）

1. **环境文件**：复制 `cp .env.example .env`（Windows 可手动复制），填写 `DATABASE_URL`、`REDIS_URL` 以及上文中的 `JWT_SECRET`、`CREDENTIAL_CODE_PEPPER`、`BIND_CREDENTIAL_API_KEY`。

2. **依赖**：`npm install`

3. **数据库迁移**：

   ```bash
   npx prisma migrate deploy
   ```

   开发环境可使用 `npm run db:migrate`。

4. **首个管理员**（在 `.env` 中设置 `SEED_ADMIN_PASSWORD`，至少 12 位）：

   ```bash
   npm run db:seed
   ```

5. **启动 Web**：`npm run dev`，默认 <http://localhost:3000>。

6. **Phase 7（资料上传、队列解析、Agent 课程 RAG）** — 按需完成；不做则课程资料相关能力不可用。

7. **Phase 8（课程聊天与 QA 日志）** — 按需完成；不做则聊天、统计与 GDPR 导出不可用。  
   - 配置 **`EDU_AGENT_BASE_URL`**（及按需 **`EDU_AGENT_API_KEY`**），并**常驻**运行仓库根的 **`edu-gateway`**（见上文「Phase 8」表）。  
   - 保证数据库已应用含 **`qa_logs`** 的迁移。  
   - 使用聊天前用户须完成 **Agent 身份绑定**（凭证流程不变）。

   **6.1 基础设施**  
   若用本仓库 [`docker-compose.yml`](docker-compose.yml)：先 `docker compose up -d`，保证 PostgreSQL、**Redis**、**MinIO** 已就绪（见上文「用 Docker 启动」）。

   **6.2 写入 `edu-platform/.env`**  
   - **`MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MINIO_BUCKET` / `MINIO_USE_SSL`**：与 MinIO 实例一致；缺任一项时，资料上传等接口会 **503**。  
   - **`INTERNAL_API_KEY`**（≥16 字符）：内部接口 `GET /api/v1/internal/course-rag-access` 使用请求头 **`X-Internal-Key`**；未配置时该接口 **503**。Agent 侧 `EDU_PLATFORM_INTERNAL_API_KEY` 须与此相同。  
   可选 Stream 相关变量见上文「Phase 7：资料存储、队列、内部鉴权与 Agent」表格。

   **6.3 在 MinIO 中创建 Bucket（名称必须与 `MINIO_BUCKET` 一致）**  
   1. 浏览器打开 MinIO 控制台（compose 默认 <http://localhost:9001>）。  
   2. 使用 **`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`** 登录控制台（见 `docker-compose.yml`；默认用户 `minio`、密码 `minio_minio_minio`）。若 `.env` 按上文示例填写，**`MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`** 与这对 root 凭据相同，用于 Next 与 Worker 走 S3 API；自建 MinIO 时以你实际颁发的密钥为准。  
   3. 左侧 **Buckets** → **Create Bucket** → **Bucket Name** 填成与 `.env` 中 **`MINIO_BUCKET` 完全相同**（例如 `edu-materials`）→ 创建。  
   若未创建同名 Bucket，资料上传会在写入对象存储时失败。

   **6.4 另开终端运行 `edu-rag-worker`（消费 Redis Stream，解析并入索引）**  
   - **目录**：在**仓库根目录**（与 `edu-platform` 同级、含 `pyproject.toml`），不是 `edu-platform` 内。  
   - **依赖**：`uv sync`（首次或依赖变更后）。  
   - **环境变量**：Worker **不会**自动读取 `edu-platform/.env`。请在当前终端中导出与平台一致的 **`DATABASE_URL`**、**`REDIS_URL`**、全部 **`MINIO_*`**（以及你修改过的 `RAG_TASK_STREAM_NAME` 等）；嵌入与 Ollama 等仍按仓库根 **`src/rag_mvp/config.py`** / 根目录 `.env` 说明配置。  
   - **启动**：

     ```bash
     edu-rag-worker
     ```

     若 `edu-rag-worker` 不在 PATH，可用 `uv run edu-rag-worker`。  
   - **含义**：Next 在上传资料成功后会向 Redis Stream **`XADD`** 任务；本进程用 **`XREADGROUP`** 取任务、解析文件并写入 **`LIGHTRAG_*`**。不启动 Worker 时，资料会长期停在排队/解析类状态。更细的契约见 [`implement_docs/phase7.md`](../implement_docs/phase7.md)；管线代码见仓库根 `src/rag_mvp/material_processor.py`、`src/rag_mvp/worker.py`。

## 前端页面（Phase 7 + Phase 8）

- `/courses`：课程列表（教师看自己的课、学生看已加入的课）
- `/courses/[courseId]`：课程详情、发布/加入、资料列表（轮询状态）、教师上传与删除资料；**已发布**课程下入口：**课程问答**、教师 **教学数据**
- `/courses/[courseId]/chat`：**课程聊天**（SSE B3 流式；首次进入可弹采集说明）
- `/courses/[courseId]/analytics`：**教师课程聚合统计**（需任课教师或管理员）
- `/me/progress`：**学生学习进度**（启发式汇总自 `qa_logs`）

需登录；路由由 `middleware.ts` 保护。

## 主要 API（契约见 `implement_docs/phase6.md`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/register` | 注册（禁止注册为 ADMIN）；**学生**响应内含一次性 `credential.code` |
| POST | `/api/v1/login` | 登录，设置 HttpOnly `edu_access` Cookie，并返回 `token` / `refresh_token` |
| POST | `/api/v1/refresh` | 刷新访问令牌（refresh 单次消费事务化） |
| GET/PUT | `/api/v1/user` | 当前用户（支持 `Authorization: Bearer` 或 Cookie）；**PUT** 可更新 **`qa_collection_enabled`**、**`qa_collection_notice_accepted`**（Phase 8 采集与告知） |
| GET | `/api/v1/credentials` | **仅学生**：本人凭证列表（无明文 code） |
| POST | `/api/v1/credentials` | **403**（关闭自助生成；凭证由注册或管理员发放） |
| GET/POST | `/api/v1/admin/credentials` | 管理员凭证（需 ADMIN） |
| DELETE | `/api/v1/admin/credentials/{id}` | 撤销未使用凭证 |
| POST | `/api/v1/bind/start` | Agent：提交 `code`，返回 `bind_challenge_token`（需 `X-Platform-Bind-Key` + **Redis**） |
| POST | `/api/v1/bind/complete` | Agent：提交 challenge + `agent_user_id` + `channel`，完成绑定 |

### Phase 7 课程与资料（契约见 `implement_docs/phase7.md`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET / POST | `/api/v1/courses` | 列表 / 创建课程（**仅教师**可创建） |
| GET / PATCH / DELETE | `/api/v1/courses/{courseId}` | 详情 / 更新 / 软删（教师为本课教师；学生需已选课才可读） |
| POST | `/api/v1/courses/{courseId}/publish` | 发布（仅教师） |
| POST | `/api/v1/courses/{courseId}/archive` | 归档（仅教师） |
| POST | `/api/v1/courses/{courseId}/join` | 学生加入（**仅 PUBLISHED**） |
| GET / POST | `/api/v1/courses/{courseId}/lessons` | 课时列表 / 创建（读需成员；写需教师） |
| PATCH / DELETE | `/api/v1/courses/{courseId}/lessons/{lessonId}` | 更新 / 软删课时 |
| GET / POST | `/api/v1/courses/{courseId}/materials` | 资料列表（成员）/ **multipart 上传**（仅教师）；**仅允许**扩展名 **`pdf` / `md` / `txt`**；`REDIS_URL` 必填；上传成功后 **`XADD`** 入队（带**有限次重试**） |
| DELETE | `/api/v1/materials/{materialId}` | 删除资料（仅教师）：**软删** → **入队** `delete_material`（重试）→ **再删 MinIO**；若入队仍失败，行上写入 `statusMessage`（`RAG_DELETE_QUEUE_FAILED:…`）并返回 503，需运维补偿或修 Redis 后重试 |
| GET | `/api/v1/internal/course-rag-access` | 内部：`course_id`、`user_id` 查询参数；请求头 **`X-Internal-Key: INTERNAL_API_KEY`**；返回 `{ access: boolean }` |

### Phase 8 聊天、统计与隐私（契约见 `implement_docs/phase8.md`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/courses/{courseId}/chat` | **SSE**（`text/event-stream`），body：`{ message, lesson_id? }`；需 **`EDU_AGENT_BASE_URL`**、JWT **`agent_user_id`**；代理 EduAgent 流并可选写 **`qa_logs`** |
| GET | `/api/v1/courses/{courseId}/chat/history` | 分页 `limit` / `offset`；**学生**仅本人；**教师**禁止逐条明细；**管理员**可加 **`?student_id=`** 审计 |
| GET | `/api/v1/courses/{courseId}/analytics` | 课程聚合（总问答数、耗时、热点问题、活跃学生、资料命中等）；**任课教师**或 **ADMIN** |
| GET | `/api/v1/students/{studentId}/learning-progress` | **本人**或 **ADMIN** |
| GET | `/api/v1/me/qa-logs/export` | 导出当前用户 **`qa_logs`**（JSON，GDPR） |
| DELETE | `/api/v1/me/qa-logs` | 软删当前用户全部 **`qa_logs`**（`deleted_at`） |

## 测试

```bash
npm test
```

## 生产构建

```bash
npm run build
npm start
```

## 安全提示

- 勿在日志中打印密码或凭证码明文。
- `BIND_CREDENTIAL_API_KEY`、`JWT_SECRET`、`CREDENTIAL_CODE_PEPPER`、`INTERNAL_API_KEY`、MinIO 密钥仅保存在服务端环境变量中；**勿将 `INTERNAL_API_KEY` 暴露给浏览器**。
- 生产环境务必为 **`REDIS_URL`** 配置密码与网络隔离；绑定 challenge 与 RAG 队列均依赖 Redis。
- 课程资料列表与下载路径均校验 **选课或任课教师**；Agent 查课程 RAG 前须通过内部接口确认访问权；**`knowledge_query` 不得省略 `sources`**，且 `sources` 为 `course` / `all` 时会话须已绑定课程。
- **`INTERNAL_API_KEY`** 仅服务服务端与 Agent；**`EDU_AGENT_API_KEY`**（若启用）勿暴露给浏览器。聊天与落库均在 **Route Handler** 内完成，浏览器只收本平台 SSE。
