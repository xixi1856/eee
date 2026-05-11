# 教育平台系统架构文档

> 受众：开发者 / 架构设计师  
> 代码库路径：`edu-platform/`  
> 文档生成依据：源码（Prisma Schema、Service 层、API Route、lib/）

---

## 1. 项目概览

### 目标

为师生提供一套 **课程管理 + AI 辅助学习** 的闭环平台：

- 学生通过凭证码绑定 AI Agent 身份，进入课程聊天室，获得基于课程 RAG 知识库的智能问答。
- 教师上传学习材料，触发后台 RAG 索引流水线，并通过数据面板查看学生学习行为聚合数据。

### 核心功能

| 功能域 | 关键能力 |
|---|---|
| B1 用户与凭证绑定 | 注册/登录、JWT 双 token、凭证码生成与二步绑定、IP 限速 |
| B2 课程资料与 RAG | 课程/课时管理、材料上传(MinIO)、Redis Stream 异步 RAG 索引 |
| B3 课程聊天与分析 | SSE 流式聊天、QaLog 埋点、教师数据面板、隐私开关 |

---

## 2. 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                          浏览器 / API 客户端                          │
│   Cookie: edu_access (JWT)  或  Authorization: Bearer <token>        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Next.js 15 App (edu-platform)                     │
│                                                                     │
│  ┌─────────────────┐   ┌───────────────────────────────────────┐    │
│  │  Next.js        │   │       API Routes /api/v1/...          │    │
│  │  Middleware     │   │                                       │    │
│  │  (JWT 路由守卫)  7│   │  B1 /auth  /bind  /credentials       │    │
│  └────────┬────────┘   │  B2 /courses  /materials             │    │
│           │            │  B3 /courses/[id]/chat  /analytics   │    │
│           │            │     /internal/course-rag-access       │    │
│           ▼            └──────────────────┬────────────────────┘    │
│     页面路由                               │                          │
│  /login  /courses                         │                          │
│  /credentials  /me                        ▼                          │
│                                  ┌────────────────┐                 │
│                                  │  Service 层     │                 │
│                                  │ authService     │                 │
│                                  │ credentialSvc   │                 │
│                                  │ courseService   │                 │
│                                  │ materialService │                 │
│                                  │ chatService     │                 │
│                                  │ analyticsService│                 │
│                                  └───────┬─────────┘                │
└──────────────────────────────────────────┼──────────────────────────┘
                                           │
              ┌────────────────────────────┼───────────────────┐
              │                            │                   │
              ▼                            ▼                   ▼
   ┌──────────────────┐      ┌──────────────────┐  ┌─────────────────┐
   │  PostgreSQL 16   │      │   Redis 7        │  │   MinIO (S3)    │
   │  (pgvector)      │      │  · bind challenge│  │  materials/     │
   │  · users         │      │  · 绑定限速 ZSET  │  │  {courseId}/   │
   │  · credentials   │      │  · RAG task      │  │  {materialId}/ │
   │  · courses       │      │    Stream        │  │  {filename}     │
   │  · materials     │      └──────────────────┘  └─────────────────┘
   │  · qa_logs       │                 │
   │  · ...           │                 │ xRead (Redis Stream)
   └──────────────────┘                 ▼
                              ┌──────────────────┐
                              │  Python RAG      │
                              │  Worker          │
                              │  · parse PDF/MD  │
                              │  · chunk & embed │
                              │  · index to RAG  │
                              │    storage       │
                              └──────────────────┘

                         ┌─────────────────────────┐
                         │  EduAgent (Python)       │
                         │  BASE_URL: EDU_AGENT_*   │
                         │  · POST /v1/sessions     │
                         │  · POST /v1/chat/        │
                         │    completions (SSE)     │
                         │  · 调用 /internal/       │
                         │    course-rag-access      │
                         │    验证课程访问权限        │
                         └─────────────────────────┘
```

---

## 3. 模块说明

### 3.1 B1 — 用户与凭证码绑定

#### 模块功能

将平台用户账号（`users`）与外部 AI Agent 身份（`agent_user_id`）通过一次性凭证码完成**双向绑定**，绑定后 JWT Access Token 中携带 `agent_user_id`，聊天时直接路由到正确的 Agent 用户上下文。

#### 数据模型

```
users ─── credentials (1:N)
users ─── agent_identity_mappings (1:1)
users ─── refresh_tokens (1:N)
users ─── credential_bind_attempts (via IP, 限速记录)
```

#### 关键流程

**注册流程**
```
POST /api/v1/register
  → assertPasswordPolicy (≥8字符, 满足3类字符类)
  → argon2id hash password
  → DB Transaction:
      create User
      if role=STUDENT → allocateStudentCredential (HMAC-SHA256 pepper, 8字符随机码)
  → 返回 user + credential.code (仅此一次明文)
```

**登录流程**
```
POST /api/v1/login
  → findUser by username (isActive=true)
  → argon2id.verify(passwordHash, plainPassword)
  → loadAgentUserId (查 agent_identity_mappings)
  → signAccessToken (HS256, TTL=15min, payload: sub/username/role/agent_user_id)
  → generateRefreshToken → SHA256 hash → 存 refresh_tokens (TTL=7d)
  → 返回 {token, refresh_token, user}
```

**凭证码二步绑定流程（由 Agent 发起）**
```
Step 1: POST /api/v1/bind/start
  Header: X-Platform-Bind-Key (constant-time 比对 BIND_CREDENTIAL_API_KEY)
  Body:   { code: "XXXXXXXX" }
  → IP 限速 assertBindAttemptAllowed (Redis ZSET, 滑动窗口1h)
  → HMAC-SHA256(pepper, code) → 查 credentials 表
  → 生成 challenge_token (32字节随机hex)
  → Redis SET bind:ch:{token} = codeHash (TTL=BIND_CHALLENGE_TTL_SEC)
  → 返回 { challenge_token }

Step 2: POST /api/v1/bind/complete
  Header: X-Platform-Bind-Key
  Body:   { bind_challenge_token, agent_user_id, channel }
  → Redis GETDEL bind:ch:{token} → 取回 codeHash (原子消费)
  → DB Transaction:
      credential.status = USED, boundAt = now, boundAgentUserId = agent_user_id
      upsert AgentIdentityMapping(platformUserId ↔ agentUserId, channel)
  → signChannelToken (HS256, TTL=1h, payload: platform_user_id/agent_user_id/channel)
  → 返回 { channel_token, platform_user_id }
```

#### 限速机制

| 维度 | 存储 | 策略 |
|---|---|---|
| 凭证生成频率 | Postgres `credentials` | 每用户每小时 ≤ `CREDENTIAL_GEN_LIMIT_PER_HOUR`(默认10) |
| 绑定失败次数 | Redis ZSET `bind:failz:{ip}` | 每IP每小时 ≤ `BIND_FAIL_LIMIT_PER_HOUR`(默认20) |
| IP 封禁 | Redis KEY `bind:ban:{ip}` | 超限后封禁 `BIND_BAN_MINUTES`(默认15min) |
| Redis 不可用时 | Postgres `credential_bind_attempts` | 降级到 Prisma 计数查询 |

#### 关键接口

| 路径 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/api/v1/register` | POST | 无 | 注册（ADMIN 角色被硬拒） |
| `/api/v1/login` | POST | 无 | 登录，返回双 token |
| `/api/v1/refresh` | POST | 无 | Refresh token 轮换 |
| `/api/v1/bind/start` | POST | `X-Platform-Bind-Key` | 凭证码第一步 |
| `/api/v1/bind/complete` | POST | `X-Platform-Bind-Key` | 凭证码第二步，完成绑定 |
| `/api/v1/credentials` | GET | JWT | 查看我的凭证列表 |
| `/api/v1/admin/credentials` | GET/POST | JWT (ADMIN) | 管理员管理凭证 |

---

### 3.2 B2 — 课程资料与 RAG

#### 模块功能

教师管理课程结构（课程 → 课时 → 材料），上传的文件经 **MinIO 持久化 + Redis Stream 异步分发**，由 Python RAG Worker 完成解析与向量索引，最终供 EduAgent 查询检索。

#### 数据模型

```
courses (DRAFT/PUBLISHED/ARCHIVED)
  └── lessons (1:N, orderIndex 排序)
        └── materials (N:1, lessonId 可为空)

materials.status: UPLOADED → PARSING → PARSED → INDEXING → READY | FAILED
```

#### 材料上传流程

```
POST /api/v1/courses/{courseId}/materials  (multipart/form-data)
  → JWT 鉴权 + assertTeacherOfCourse
  → 验证文件类型: 仅允许 pdf / md / txt
  → 验证文件大小: ≤ MATERIAL_MAX_UPLOAD_BYTES
  → Redis 可用性检查 (REDIS_URL 必须存在)
  → Prisma: create Material (status=UPLOADED, minioPath=materials/{courseId}/{materialId}/{safeName})
  → putObjectStream → MinIO (AWS S3 SDK, path-style, forcePathStyle=true)
  → 失败时: Material.status = FAILED
  → 成功时: redis.xAdd(RAG_TASK_STREAM, "*", { task_id, material_id, operation="parse_and_index", created_at })
             (失败重试最多5次, 每次递增延迟 200ms*i)
  → 返回 { id, original_filename, status, created_at }
```

#### RAG 任务队列

```
Redis Stream: RAG_TASK_STREAM (默认 "edu:rag:tasks")

消息字段:
  task_id     : UUID
  material_id : UUID (worker 从 DB 读取 minioPath/courseId)
  operation   : "parse_and_index" | "delete_material"
  created_at  : ISO8601

Worker (Python, edu-platform 外部):
  xRead → 从 MinIO 下载文件 → 解析文本 → 切块 → 向量化 → 写入 RAG Storage
  → 回调更新 Material.status (READY / FAILED) + indexedChunkCount
```

#### 内部 RAG 访问鉴权

```
GET /api/v1/internal/course-rag-access?course_id=&user_id=
  Header: X-Internal-Key: INTERNAL_API_KEY
  逻辑:
    user_id = agent_user_id → 查 agent_identity_mappings → platformUserId
    hasCourseRagAccess(platformUserId, courseId):
      course.teacherId == userId  OR  courseEnrollments 存在
  返回: { access: boolean }

用途: EduAgent 在 RAG 检索前调用此接口确认用户是否有权访问该课程知识库。
```

#### 关键接口

| 路径 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/api/v1/courses` | GET/POST | JWT | 列举/创建课程 |
| `/api/v1/courses/{id}` | GET/PATCH | JWT | 课程详情/更新 |
| `/api/v1/courses/{id}/publish` | POST | JWT (Teacher) | 发布课程 |
| `/api/v1/courses/{id}/lessons` | GET/POST | JWT | 课时管理 |
| `/api/v1/courses/{id}/materials` | GET/POST | JWT | 材料列表/上传 |
| `/api/v1/courses/{id}/join` | POST | JWT (Student) | 加入课程 |
| `/api/v1/internal/course-rag-access` | GET | `X-Internal-Key` | Agent RAG 访问鉴权 |

---

### 3.3 B3 — 课程聊天与数据采集

#### 模块功能

学生在课程内发起 AI 问答，平台通过 SSE 流式转发 EduAgent 的响应，同步解析引用来源（citation），并将完整问答记录（QaLog）入库供教师分析。

#### 聊天数据流

```
学生浏览器
    │  POST /api/v1/courses/{courseId}/chat
    │  Body: { message: "...", lesson_id?: "..." }
    │  Cookie: edu_access (JWT, 含 agent_user_id)
    ▼
[Chat Route Handler]
    → requireAuthenticated + getCourseIfMember
    → 检查 auth.agent_user_id (未绑定 → 400 AGENT_NOT_BOUND)
    → 验证 lesson_id (若有)
    ▼
[chatService.courseChatSseResponse]
    → getOrCreateCourseChatSession:
        查 course_chat_sessions (courseId + studentId 唯一索引)
        不存在 → POST {AGENT_BASE_URL}/v1/sessions
               Body: { user_id: agentUserId, title: "course:{courseId}" }
               → 存入 DB
    ▼
    → postChatCompletionsStream:
        POST {AGENT_BASE_URL}/v1/chat/completions?session_id=&user_id=
        Headers: X-Platform-User-Id / X-Platform-Course-Id / X-Platform-Lesson-Id
        Body: { model:"", messages:[{role:"user", content:message}], stream:true }
    ▼
[createB3SseTransformFromAgent — TransformStream]
    Agent SSE (OpenAI格式)           B3 SSE (平台格式)
    ─────────────────────────────    ────────────────────────────────
    choices[0].delta.content    →   { type:"text", content:"..." }
    edu_meta.b3 (累积)          →   (内部状态)
    [DONE]                      →   citations: { type:"citation", chunk_id, material_id, source_label }
                                    { type:"done", tokens, exec_time_ms, error? }
    ▼
[Stream flush → maybePersistQaLog]
    if user.qaCollectionEnabled == true AND b3 数据存在:
        INSERT qa_logs (question, answer, tokens, exec_time_ms,
                        hit_chunks[], hit_materials[], hit_sources[],
                        model_used, course_id, student_id, lesson_id, session_id)
    ▼
学生浏览器 (EventSource 接收 SSE)
```

#### QaLog 数据埋点字段

| 字段 | 来源 | 说明 |
|---|---|---|
| `question` | 用户输入 | 原始问题文本 |
| `answer` | Agent SSE delta 累积 | 完整回答文本 |
| `question_tokens` / `answer_tokens` / `total_tokens` | `edu_meta.b3` | Token 消耗 |
| `execution_time_ms` | `edu_meta.b3` | Agent 执行时长 |
| `model_used` | `edu_meta.b3` | 模型标识（截取100字符） |
| `hit_chunks[]` | `edu_meta.b3.hit_chunks` | 命中的 RAG chunk ID |
| `hit_materials[]` | `edu_meta.b3.hit_materials` | 命中的材料 ID |
| `hit_sources[]` | `edu_meta.b3.hit_sources` | 引用来源标签 |

#### 隐私控制

```
User.qaCollectionEnabled = true  (默认)
  → 问答结束后写入 qa_logs
User.qaCollectionEnabled = false
  → 聊天正常进行，但不写入 qa_logs
User.qaCollectionNoticeAcceptedAt
  → 用户明确接受数据采集通知的时间戳
```

#### 教师数据面板

```
GET /api/v1/courses/{courseId}/analytics?start_date=&end_date=
  → 鉴权: TEACHER (必须是该课程教师) 或 ADMIN
  → 默认时间窗口: 最近7天

返回聚合数据 (不含逐条学生 Q&A 原始内容):
  total_questions      : 总问答数
  avg_response_time_ms : 平均响应时长
  top_questions[]      : 高频问题 (TOP 15, 含 avg_quality)
  active_students[]    : 活跃学生 (TOP 20, 含 question_count / last_active / name)
  top_materials[]      : 被命中最多的材料 (TOP 15, via unnest(hit_materials))
  weak_concepts[]      : 薄弱概念 (预留字段, 当前返回空数组)
```

#### 关键接口

| 路径 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/api/v1/courses/{id}/chat` | POST | JWT (Student, 已绑定) | 课程聊天 (SSE) |
| `/api/v1/courses/{id}/analytics` | GET | JWT (Teacher/Admin) | 教师数据面板 |
| `/api/v1/me/progress` | GET | JWT | 学生个人学习进度 |

---

## 4. 模块间调用关系与数据流

### 4.1 完整请求路径（学生聊天）

```
Student
  │
  ├─① POST /api/v1/courses/{id}/chat
  │     Cookie: edu_access (JWT含 agent_user_id)
  │
  ▼
Next.js Middleware
  │  verifyAccessToken → 检查路由权限
  ▼
Chat API Route
  │  requireAuthenticated → getCourseIfMember → 验证 agent_user_id
  ▼
chatService
  ├─② GET course_chat_sessions (Postgres)
  │     不存在 →
  ├─③ POST {AGENT_URL}/v1/sessions       [Platform → EduAgent]
  │     create session, 写 DB
  ├─④ POST {AGENT_URL}/v1/chat/completions?session_id=&user_id=   [SSE]
  │     Headers: X-Platform-Course-Id, X-Platform-Lesson-Id
  │
  ▼  EduAgent 内部（外部系统）
     ├─⑤ GET /api/v1/internal/course-rag-access  [EduAgent → Platform]
     │     验证用户是否有课程 RAG 访问权
     ├─⑥ 查询 RAG Storage（向量检索）
     └─⑦ 调用 LLM，流式输出 SSE
  │
  ▼
createB3SseTransformFromAgent (TransformStream)
  │  解析 OpenAI SSE → B3 SSE (text / citation / done)
  │  流式转发给浏览器
  │
  ├─⑧ 流结束后: INSERT qa_logs (Postgres)  [如 qaCollectionEnabled]
  │
  ▼
Student Browser (SSE EventSource)
```

### 4.2 材料上传与 RAG 索引流程

```
Teacher
  │
  ├─① POST /api/v1/courses/{id}/materials (multipart)
  │
  ▼
materialService
  ├─② assertTeacherOfCourse (Postgres)
  ├─③ create Material (status=UPLOADED, Postgres)
  ├─④ putObjectStream → MinIO
  │     路径: materials/{courseId}/{materialId}/{filename}
  └─⑤ redis.xAdd(RAG_TASK_STREAM, { material_id, operation })

Python RAG Worker (异步, 独立进程)
  ├─⑥ redis.xRead(RAG_TASK_STREAM)
  ├─⑦ 从 MinIO 下载文件
  ├─⑧ 解析 (PDF→text / MD→text)
  ├─⑨ 切块 + 向量化 + 写入 RAG Storage
  └─⑩ 更新 Material.status = READY / FAILED
        + indexedChunkCount
```

### 4.3 绑定凭证流程

```
EduAgent (Agent系统)
  │
  ├─① POST /api/v1/bind/start
  │     Header: X-Platform-Bind-Key
  │     Body: { code }
  │     → Redis SET bind:ch:{token} = codeHash (TTL)
  │     ← { challenge_token }
  │
  ├─② POST /api/v1/bind/complete
  │     Body: { bind_challenge_token, agent_user_id, channel }
  │     → Redis GETDEL (原子消费 challenge)
  │     → Postgres: credential.USED + AgentIdentityMapping 创建
  │     ← { channel_token, platform_user_id }
  │
Student (下次登录)
  ├─③ POST /api/v1/login
  │     → loadAgentUserId (查 AgentIdentityMapping)
  │     ← JWT 含 agent_user_id → 解锁聊天功能
```

---

## 5. 技术栈与关键依赖

### 运行时环境

| 层 | 技术 | 版本 | 说明 |
|---|---|---|---|
| Web 框架 | Next.js (App Router) | ^15.1.0 | API Routes + SSR 页面 |
| 运行时 | Node.js | - | Next.js 内置 |
| 语言 | TypeScript | ^5.7.0 | 全栈类型安全 |
| UI 库 | Ant Design | ^5.23.0 | React 组件库 |

### 数据存储

| 存储 | 镜像/SDK | 用途 |
|---|---|---|
| PostgreSQL 16 | `pgvector/pgvector:pg16` | 主数据库（用户/课程/QaLog） |
| Redis 7 | `redis:7-alpine` | 绑定挑战、限速、RAG 任务队列 |
| MinIO | `minio/minio:latest` | 课程材料对象存储（S3 协议） |

### 关键 NPM 依赖

| 包 | 版本 | 用途 |
|---|---|---|
| `@prisma/client` | ^6.3.0 | ORM，Postgres 访问 |
| `jose` | ^5.9.6 | JWT 签发/验证（HS256） |
| `argon2` | ^0.41.1 | 密码哈希（argon2id） |
| `redis` | ^4.7.1 | Redis 客户端（Stream/ZSET） |
| `@aws-sdk/client-s3` | ^3.1045.0 | MinIO/S3 对象存储操作 |

### 外部服务

| 服务 | 协议 | 环境变量 | 说明 |
|---|---|---|---|
| EduAgent | HTTP REST + SSE | `EDU_AGENT_BASE_URL` / `EDU_AGENT_API_KEY` | Python AI Agent，提供 `/v1/sessions` 和 `/v1/chat/completions` |
| Python RAG Worker | Redis Stream | `REDIS_URL` / `RAG_TASK_STREAM` | 独立进程，消费队列做 RAG 索引 |

---

## 6. 安全设计要点

| 机制 | 实现 |
|---|---|
| 密码哈希 | `argon2id`（内存困难，抗 GPU 暴力破解） |
| JWT | HS256，Access TTL=15min，Refresh TTL=7d，Channel TTL=1h |
| Refresh Token | SHA256 摘要存储，rotate-on-use（旧 token 立即撤销） |
| 凭证码哈希 | HMAC-SHA256 + 服务端 pepper（`CREDENTIAL_CODE_PEPPER`） |
| Bind API Key | `timingSafeEqual` 常数时间比对，防时序攻击 |
| IP 限速 | Redis 滑动窗口 + 封禁键，降级到 Postgres |
| 管理员注册 | API 层硬拒 `role=ADMIN`，必须通过 seed/数据库直接创建 |
| 内部 API | `X-Internal-Key` 保护 `/internal/*`，防止未授权 RAG 查询 |
| 路由守卫 | Next.js Middleware 对 `/courses/*`、`/credentials/*`、`/user/*` 强制 JWT 验证 |
| 数据采集隐私 | `qaCollectionEnabled` 字段，用户可随时关闭，不影响聊天功能 |

---

## 7. 数据库核心模型关系

```
User (1) ──── (N) Credential
User (1) ──── (1) AgentIdentityMapping
User (1) ──── (N) RefreshToken
User (1) ──── (N) QaLog [as student]
User (1) ──── (N) Course [as teacher]
User (M) ──── (N) Course [via CourseEnrollment]

Course (1) ──── (N) Lesson
Course (1) ──── (N) Material
Course (1) ──── (N) QaLog
Course (1) ──── (N) CourseChatSession [每学生一条]

Lesson (1) ──── (N) Material
Lesson (1) ──── (N) QaLog

QaLog.hit_materials[] → Material.id (非外键, 数组字段, 用于 analytics unnest)
```

---

## 8. 扩展与改进点

| 方向 | 说明 |
|---|---|
| `weak_concepts` | `analyticsService` 中已预留字段（当前返回 `[]`），待 NLP 概念提取实现 |
| 材料状态回调 | RAG Worker 目前需要直接写 DB；可改为 Worker 调用平台 Webhook，解耦依赖 |
| RAG Worker 扩展 | Redis Stream 支持 Consumer Group，可横向扩展多个 Worker 实例 |
| 凭证码格式 | 当前 8 字符 Base62，如需更强安全可调整长度或引入校验位 |
| QaLog 软删除 | 表中有 `deleted_at` 字段（analytics SQL 已过滤），尚未暴露用户删除接口 |
| `response_quality` | `avg_quality` 已在 analytics SQL 中聚合，但写入路径尚未实现（来源可为学生反馈/LLM 评分） |
| Channel Token 使用 | `signChannelToken` 已实现，但当前返回给 Agent 后的具体用途由 Agent 侧决定 |
