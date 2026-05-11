# 后端接口文档 — EduPlatform API

> 版本：v1  
> Base URL：`/api/v1`  
> 受众：前端开发者、测试工程师、新加入后端开发者

---

## 目录

- [概览与约定](#概览与约定)
- [认证机制](#认证机制)
- [通用错误格式](#通用错误格式)
- [M1 认证与账户](#m1-认证与账户)
- [M2 凭证码管理](#m2-凭证码管理)
- [M3 Agent 身份绑定](#m3-agent-身份绑定)
- [M4 课程管理](#m4-课程管理)
- [M5 课时管理](#m5-课时管理)
- [M6 课程材料与 RAG](#m6-课程材料与-rag)
- [M7 课程聊天（B3 SSE）](#m7-课程聊天b3-sse)
- [M8 学习数据与分析](#m8-学习数据与分析)
- [M9 内部接口（服务间调用）](#m9-内部接口服务间调用)
- [枚举值参考](#枚举值参考)

---

## 概览与约定

### 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | Next.js 15 App Router（API Routes） |
| 语言 | TypeScript 5.7 |
| ORM | Prisma 6 + PostgreSQL 16 |
| 缓存/队列 | Redis 7（限速、绑定挑战、RAG 任务 Stream） |
| 对象存储 | MinIO（S3 协议，AWS SDK） |
| 密码哈希 | argon2id |
| JWT | jose（HS256） |

### 请求规范

- 所有接口 **Content-Type** 默认为 `application/json`，上传接口使用 `multipart/form-data`。
- 认证通过 **Cookie** `edu_access` 或 **`Authorization: Bearer <token>`** 传递 Access Token，两者等效。
- 路径中 `{courseId}`、`{lessonId}` 等均为 **UUID v4** 格式。

### 响应规范

**成功响应**（HTTP 2xx）：

```json
{ "key": "value" }
```

**失败响应**（HTTP 4xx / 5xx）：

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "人类可读描述",
    "details": {}
  }
}
```

---

## 认证机制

### Access Token（短期）

- 算法：HS256，由 `JWT_SECRET` 签名
- 默认 TTL：**15 分钟**（`JWT_ACCESS_TTL_SEC`）
- 存储：响应 Body 中返回，同时写入 `edu_access` HttpOnly Cookie
- Payload 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `sub` | `string` | 平台用户 UUID |
| `username` | `string` | 用户名 |
| `role` | `STUDENT\|TEACHER\|ADMIN` | 角色 |
| `agent_user_id` | `string?` | 绑定的 Agent 身份 ID（绑定后才有） |

### Refresh Token（长期）

- 默认 TTL：**7 天**（`JWT_REFRESH_TTL_SEC`）
- 存储：SHA256 摘要存库，rotate-on-use（每次刷新自动轮换）
- 在 `POST /api/v1/refresh` 使用

### 角色权限矩阵

| 角色 | 说明 |
|---|---|
| `STUDENT` | 学生：加入课程、使用聊天、管理个人凭证 |
| `TEACHER` | 教师：创建/管理课程、上传材料、查看分析面板 |
| `ADMIN` | 管理员：管理所有凭证、查看任意学生进度（不可通过注册创建） |

---

## 通用错误格式

### 错误码表

| HTTP 状态码 | `code` | 说明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 请求参数格式或值不合法 |
| 401 | `UNAUTHORIZED` | 未认证或 token 无效/过期 |
| 403 | `FORBIDDEN` | 已认证但权限不足 |
| 403 | `AGENT_NOT_BOUND` | 使用聊天前需先绑定 Agent 身份 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 409 | `CONFLICT` | 资源已存在或状态冲突 |
| 429 | `RATE_LIMITED` | 触发速率限制 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |
| 502 | `AGENT_CHAT_FAILED` | Agent 服务响应异常 |
| 503 | `SERVICE_UNAVAILABLE` | 依赖服务不可用（Redis/MinIO/Agent） |

---

## M1 认证与账户

### POST `/api/v1/register` — 注册

**描述**：创建新用户账号。学生注册时系统自动生成一个一次性凭证码（明文仅在此响应中出现一次）。ADMIN 角色**不允许**通过此接口创建。

**认证**：无

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | `string` | ✓ | 用户名，需唯一 |
| `email` | `string` | ✓ | 邮箱，需唯一 |
| `password` | `string` | ✓ | 密码（≥8字符，满足3种字符类：大写/小写/数字/特殊字符） |
| `role` | `"STUDENT"\|"TEACHER"` | ✓ | 角色 |

**响应**：`201 Created`

```json
{
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "alice",
    "email": "alice@example.com",
    "role": "STUDENT",
    "real_name": null,
    "avatar_url": null,
    "qa_collection_enabled": true,
    "qa_collection_notice_accepted_at": null
  },
  "credential": {
    "code": "Abc12345",
    "expires_at": "2026-05-11T12:30:00.000Z",
    "status": "ACTIVE"
  }
}
```

> `credential` 字段仅在 `role=STUDENT` 时返回，教师注册无此字段。

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 参数缺失或密码强度不足 |
| 409 | `CONFLICT` | 用户名或邮箱已被注册 |

---

### POST `/api/v1/login` — 登录

**描述**：用户名密码登录，返回双 Token。同时将 Access Token 写入 `edu_access` Cookie。

**认证**：无

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `username` | `string` | ✓ | 用户名 |
| `password` | `string` | ✓ | 密码 |

**响应**：`200 OK`

```json
{
  "token": "eyJhbGciOiJIUzI1NiJ9...",
  "refresh_token": "d3e4f5a6b7c8...",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "alice",
    "email": "alice@example.com",
    "role": "STUDENT",
    "real_name": null,
    "avatar_url": null,
    "qa_collection_enabled": true,
    "qa_collection_notice_accepted_at": null
  }
}
```

**Cookie**：响应同时设置 `edu_access=<token>`（HttpOnly）

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 用户名或密码错误 |

---

### POST `/api/v1/refresh` — 刷新 Token

**描述**：使用 Refresh Token 换取新的 Access Token 和 Refresh Token（rotate-on-use，旧 Refresh Token 立即失效）。

**认证**：无

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `refresh_token` | `string` | ✓ | 当前有效的 Refresh Token |

**响应**：`200 OK`

```json
{
  "token": "eyJhbGciOiJIUzI1NiJ9...",
  "refresh_token": "new_refresh_token_value..."
}
```

**Cookie**：同时更新 `edu_access` Cookie

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 401 | `UNAUTHORIZED` | Refresh Token 无效、已过期或已撤销 |

---

### GET `/api/v1/user` — 获取当前用户信息

**认证**：JWT（任意角色）

**响应**：`200 OK`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "username": "alice",
  "email": "alice@example.com",
  "role": "STUDENT",
  "real_name": "Alice Smith",
  "avatar_url": null,
  "qa_collection_enabled": true,
  "qa_collection_notice_accepted_at": "2026-05-10T08:00:00.000Z"
}
```

---

### PUT `/api/v1/user` — 更新用户资料

**认证**：JWT（任意角色）

**请求 Body**（所有字段均可选）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `real_name` | `string` | 真实姓名 |
| `avatar_url` | `string` | 头像 URL |
| `email` | `string` | 邮箱（唯一） |
| `qa_collection_enabled` | `boolean` | 是否启用问答数据采集（默认 `true`） |
| `qa_collection_notice_accepted` | `boolean` | 设为 `true` 时记录用户接受数据采集通知的时间 |

**响应**：`200 OK`，返回更新后的用户对象（同 GET /user 结构）

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 409 | `CONFLICT` | 邮箱已被占用 |

---

## M2 凭证码管理

### GET `/api/v1/credentials` — 查看我的凭证列表

**描述**：学生查看自己所有凭证码的状态（已过期的凭证在查询时会自动更新为 `EXPIRED` 状态）。

**认证**：JWT（`STUDENT` 角色专属，TEACHER/ADMIN 调用返回 403）

**响应**：`200 OK`

```json
{
  "credentials": [
    {
      "id": "c1a2b3c4-...",
      "user_id": "550e8400-...",
      "status": "USED",
      "created_at": "2026-05-10T08:00:00.000Z",
      "expires_at": "2026-05-10T08:30:00.000Z",
      "used_at": "2026-05-10T08:10:00.000Z",
      "bound_at": "2026-05-10T08:10:00.000Z",
      "bound_agent_user_id": "agent-user-xyz"
    }
  ]
}
```

**凭证状态说明**：

| `status` | 说明 |
|---|---|
| `ACTIVE` | 有效，可用于绑定 |
| `USED` | 已绑定使用 |
| `EXPIRED` | 已过期 |
| `REVOKED` | 管理员已撤销 |

---

### GET `/api/v1/admin/credentials` — 管理员列举凭证

**认证**：JWT（`ADMIN` 角色）

**Query 参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `user_id` | `string (UUID)` | 否 | 过滤指定用户的凭证 |
| `status` | `ACTIVE\|USED\|EXPIRED\|REVOKED` | 否 | 过滤状态 |

**响应**：`200 OK`

```json
{
  "credentials": [
    {
      "id": "c1a2b3c4-...",
      "user_id": "550e8400-...",
      "status": "ACTIVE",
      "created_at": "2026-05-10T08:00:00.000Z",
      "expires_at": "2026-05-10T09:00:00.000Z",
      "used_at": null,
      "bound_at": null,
      "bound_agent_user_id": null
    }
  ]
}
```

> 最多返回 500 条记录。

---

### POST `/api/v1/admin/credentials` — 管理员创建凭证

**描述**：为指定用户创建一个新凭证码。

**认证**：JWT（`ADMIN` 角色）

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `user_id` | `string (UUID)` | ✓ | 目标用户 ID |
| `expires_in_minutes` | `number` | 否 | 有效期（分钟），范围 1–525600，默认 30 |

**响应**：`200 OK`

```json
{
  "code": "Xyz98765",
  "expires_at": "2026-05-11T09:30:00.000Z",
  "status": "ACTIVE",
  "user_id": "550e8400-..."
}
```

> `code` 为明文凭证码，**只在此响应中出现一次**，请妥善传达给用户。

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | `expires_in_minutes` 超出范围 |
| 404 | `NOT_FOUND` | 目标用户不存在 |

---

### DELETE `/api/v1/admin/credentials/{id}` — 撤销凭证

**认证**：JWT（`ADMIN` 角色）

**路径参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | `string (UUID)` | 凭证 ID |

**响应**：`200 OK`

```json
{ "ok": true }
```

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 404 | `NOT_FOUND` | 凭证不存在 |
| 409 | `CONFLICT` | 凭证已被使用，无法撤销 |

---

## M3 Agent 身份绑定

> 这两个接口由 **EduAgent（外部 Python 服务）** 调用，使用独立的 API Key 认证（与用户 JWT 无关）。绑定成功后，用户下次登录的 Access Token 中将携带 `agent_user_id`，解锁聊天功能。

### POST `/api/v1/bind/start` — 开始绑定（第一步）

**描述**：Agent 提交学生凭证码，平台验证后颁发一次性 `challenge_token`。

**认证**：`X-Platform-Bind-Key: <BIND_CREDENTIAL_API_KEY>`（常量时间比对）

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | `string` | ✓ | 学生持有的 8 位凭证码 |

**请求示例**：

```http
POST /api/v1/bind/start
X-Platform-Bind-Key: your-secret-key
Content-Type: application/json

{ "code": "Abc12345" }
```

**响应**：`200 OK`

```json
{
  "challenge_token": "a1b2c3d4e5f6...（64位16进制字符串）"
}
```

> `challenge_token` 存储在 Redis 中，TTL 由 `BIND_CHALLENGE_TTL_SEC` 控制，**使用一次后立即销毁**。

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 请求体缺少 `code` |
| 401 | `UNAUTHORIZED` | API Key 错误 |
| 429 | `RATE_LIMITED` | 该 IP 绑定失败次数超限 |
| 503 | `SERVICE_UNAVAILABLE` | Redis 不可用 |

**限速规则**：
- 每个 IP 每小时绑定失败次数 ≤ `BIND_FAIL_LIMIT_PER_HOUR`（默认 20 次）
- 超限后封禁 `BIND_BAN_MINUTES`（默认 15 分钟）

---

### POST `/api/v1/bind/complete` — 完成绑定（第二步）

**描述**：Agent 使用第一步拿到的 `challenge_token` 完成绑定，平台建立 `agent_user_id ↔ platform_user_id` 映射，并返回 Channel Token。

**认证**：`X-Platform-Bind-Key: <BIND_CREDENTIAL_API_KEY>`

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `bind_challenge_token` | `string` | ✓ | 第一步返回的 challenge_token |
| `agent_user_id` | `string` | ✓ | Agent 系统中该用户的 ID |
| `channel` | `string` | ✓ | Agent 渠道标识（如 `"wechat"`、`"web"` 等） |

**响应**：`200 OK`

```json
{
  "success": true,
  "platform_user_id": "550e8400-e29b-41d4-a716-446655440000",
  "channel_token": "eyJhbGciOiJIUzI1NiJ9..."
}
```

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 400 | `BIND_INVALID` | `challenge_token` 已过期、已使用或无效 |
| 401 | `UNAUTHORIZED` | API Key 错误 |
| 429 | `RATE_LIMITED` | IP 绑定失败超限 |

---

## M4 课程管理

### `CourseSummaryDto` 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `string (UUID)` | 课程 ID |
| `name` | `string` | 课程名称 |
| `description` | `string \| null` | 描述 |
| `cover_image_url` | `string \| null` | 封面图 URL |
| `status` | `DRAFT \| PUBLISHED \| ARCHIVED` | 课程状态 |
| `created_at` / `updated_at` | `string (ISO8601)` | 时间戳 |
| `share_code` | `string \| undefined` | **仅**当当前用户为该课**主讲或协作者**且 `status === PUBLISHED` 时返回；学生响应中不出现 |

### GET `/api/v1/courses` — 获取我的课程列表

**认证**：JWT（`TEACHER` 返回自己**担任主讲**或以**协作者**身份参与的课程；`STUDENT` 返回已加入的课程）

**响应**：`200 OK`

```json
{
  "courses": [
    {
      "id": "course-uuid-...",
      "name": "计算机网络",
      "description": "从协议到实现",
      "cover_image_url": null,
      "status": "PUBLISHED",
      "created_at": "2026-05-10T08:00:00.000Z",
      "updated_at": "2026-05-10T10:00:00.000Z",
      "share_code": "A1B2C3D4E5"
    }
  ]
}
```

---

### POST `/api/v1/courses` — 创建课程

**认证**：JWT（`TEACHER` 角色）

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | ✓ | 课程名称（不能为空） |
| `description` | `string\|null` | 否 | 课程描述 |
| `cover_image_url` | `string\|null` | 否 | 封面图 URL |

**响应**：`201 Created`，返回 `CourseSummaryDto`

```json
{
  "id": "course-uuid-...",
  "name": "计算机网络",
  "description": null,
  "cover_image_url": null,
  "status": "DRAFT",
  "created_at": "2026-05-11T08:00:00.000Z",
  "updated_at": "2026-05-11T08:00:00.000Z"
}
```

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 403 | `FORBIDDEN` | 非教师角色 |

---

### GET `/api/v1/courses/{courseId}` — 获取课程详情

**认证**：JWT（课程主讲、协作者或已加入的学生）

**响应**：`200 OK`

```json
{
  "course": { ...CourseSummaryDto }
}
```

---

### PATCH `/api/v1/courses/{courseId}` — 更新课程信息

**认证**：JWT（`TEACHER`，且为课程**主讲或协作者**）

**请求 Body**（所有字段可选）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `string` | 课程名（不能为空字符串） |
| `description` | `string\|null` | 课程描述 |
| `cover_image_url` | `string\|null` | 封面图 URL |

**响应**：`200 OK`，返回 `CourseSummaryDto`

---

### DELETE `/api/v1/courses/{courseId}` — 删除课程（软删除）

**认证**：JWT（`TEACHER`，且为课程**主讲**；协作者不可删除）

**响应**：`200 OK`

```json
{ "ok": true }
```

---

### POST `/api/v1/courses/{courseId}/publish` — 发布课程

**描述**：将课程状态从 `DRAFT` 改为 `PUBLISHED`。**首次**发布时系统自动生成全局唯一的 `share_code`（若已有则保留，不覆盖）。已 `PUBLISHED` 时再次调用为幂等，直接返回当前摘要（含 `share_code`）。已 `ARCHIVED` 时不可发布。

**认证**：JWT（`TEACHER`，且为课程**主讲**）

**请求 Body**：无

**响应**：`200 OK`，返回 `CourseSummaryDto`（`status` 为 `PUBLISHED` 时含 `share_code`，仅主讲可见）

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 403 | `FORBIDDEN` | 非主讲 |
| 409 | `CONFLICT` | 课程已归档，不能发布 |

---

### POST `/api/v1/courses/{courseId}/archive` — 归档课程

**描述**：将课程状态改为 `ARCHIVED`，归档后不可再通过分享码或本接口路径新增学生。

**认证**：JWT（`TEACHER`，且为课程**主讲或协作者**）

**请求 Body**：无

**响应**：`200 OK`，返回 `CourseSummaryDto`（status 变为 `ARCHIVED`）

---

### POST `/api/v1/courses/join-by-code` — 凭分享码加入课程

**描述**：使用课程 `share_code` 加入已发布课程。学生创建选课记录；其他教师成为该课**协作者**（与主讲权限接近，见实现：协作者不可发布/删除课程）。课程须为 `PUBLISHED` 且未删除。

**认证**：JWT（`STUDENT` 或 `TEACHER`；`ADMIN` 不可用此接口）

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `share_code` | `string` | ✓ | 发布课程后由系统生成的分享码（大小写不敏感，首尾空格忽略） |

**响应**：`201 Created`

学生：

```json
{
  "course_id": "course-uuid-...",
  "enrolled_at": "2026-05-11T09:00:00.000Z",
  "role": "student"
}
```

教师（协作者）：

```json
{
  "course_id": "course-uuid-...",
  "joined_at": "2026-05-11T09:00:00.000Z",
  "role": "collaborator"
}
```

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 缺少或空的 `share_code` |
| 403 | `FORBIDDEN` | `ADMIN`；或课程未开放加入 |
| 404 | `NOT_FOUND` | 分享码无效或课程不可加入 |
| 409 | `CONFLICT` | 学生已选课 / 教师已是主讲 / 已是协作者 |

---

### POST `/api/v1/courses/{courseId}/join` — 加入课程（学生）

**描述**：学生加入一门已发布的课程（已知课程 UUID 时），课程状态必须为 `PUBLISHED`。推荐新场景使用 `POST /api/v1/courses/join-by-code`。

**认证**：JWT（`STUDENT` 角色）

**请求 Body**：无

**响应**：`201 Created`

```json
{
  "course_id": "course-uuid-...",
  "enrolled_at": "2026-05-11T09:00:00.000Z"
}
```

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 403 | `FORBIDDEN` | 课程未发布，不可加入 |
| 409 | `CONFLICT` | 已加入该课程 |

---

## M5 课时管理

### GET `/api/v1/courses/{courseId}/lessons` — 获取课时列表

**认证**：JWT（课程教师或已加入的学生）

**响应**：`200 OK`

```json
{
  "lessons": [
    {
      "id": "lesson-uuid-...",
      "course_id": "course-uuid-...",
      "title": "第一章：协议基础",
      "description": null,
      "order_index": 1,
      "created_at": "2026-05-10T08:00:00.000Z",
      "updated_at": "2026-05-10T08:00:00.000Z"
    }
  ]
}
```

> 按 `order_index` 升序排列。

---

### POST `/api/v1/courses/{courseId}/lessons` — 创建课时

**认证**：JWT（`TEACHER`，且为课程创建者）

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | `string` | ✓ | 课时标题 |
| `description` | `string\|null` | 否 | 课时描述 |
| `order_index` | `number` | ✓ | 排序索引（整数） |

**响应**：`201 Created`，返回 `LessonDto`

---

### PATCH `/api/v1/courses/{courseId}/lessons/{lessonId}` — 更新课时

**认证**：JWT（`TEACHER`，且为课程创建者）

**请求 Body**（所有字段可选）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` | `string` | 课时标题 |
| `description` | `string\|null` | 课时描述 |
| `order_index` | `number` | 排序索引 |

**响应**：`200 OK`，返回 `LessonDto`

---

### DELETE `/api/v1/courses/{courseId}/lessons/{lessonId}` — 删除课时

**认证**：JWT（`TEACHER`，且为课程创建者）

**响应**：`200 OK`

```json
{ "ok": true }
```

---

## M6 课程材料与 RAG

### GET `/api/v1/courses/{courseId}/materials` — 获取材料列表

**认证**：JWT（课程教师或已加入的学生）

**Query 参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `status` | `MaterialStatus` | 过滤材料状态（见枚举值参考） |

**响应**：`200 OK`

```json
{
  "materials": [
    {
      "id": "material-uuid-...",
      "filename": "chapter1.pdf",
      "file_type": "pdf",
      "status": "READY",
      "preview_pdf_status": "NA",
      "indexed_chunk_count": 42,
      "created_at": "2026-05-10T08:00:00.000Z",
      "status_message": null
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `preview_pdf_status` | `NA` \| `PENDING` \| `READY` \| `FAILED` | Office（ppt/doc/…）上传后为 `PENDING`，Worker 生成 `preview.pdf` 后为 `READY`；`pdf`/`md`/`txt` 为 `NA`。与 RAG `status` 独立。 |

**材料状态流转**：

```
UPLOADED → PARSING → PARSED → INDEXING → READY
                                        ↘ FAILED
```

---

### POST `/api/v1/courses/{courseId}/materials` — 上传材料

**描述**：教师上传课程材料，文件存储到 MinIO，并异步触发 RAG 索引流水线。

**认证**：JWT（`TEACHER`，且为课程创建者）

**Content-Type**：`multipart/form-data`

**表单字段**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | `File` | ✓ | 上传文件，支持格式：`pdf`、`md`、`txt`、`ppt`、`pptx`、`doc`、`docx` |
| `lesson_id` | `string (UUID)` | 否 | 关联的课时 ID |

**请求示例**：

```http
POST /api/v1/courses/{courseId}/materials
Authorization: Bearer <token>
Content-Type: multipart/form-data; boundary=----FormBoundary

------FormBoundary
Content-Disposition: form-data; name="file"; filename="chapter1.pdf"
Content-Type: application/pdf

<file binary data>
------FormBoundary--
```

**响应**：`200 OK`

```json
{
  "id": "material-uuid-...",
  "original_filename": "chapter1.pdf",
  "status": "UPLOADED",
  "created_at": "2026-05-11T09:00:00.000Z"
}
```

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 文件类型不支持（仅限 pdf/md/txt/ppt/pptx/doc/docx）或文件为空 |
| 400 | `VALIDATION_ERROR` | 文件大小超过限制（`MATERIAL_MAX_UPLOAD_BYTES`） |
| 404 | `NOT_FOUND` | `lesson_id` 不存在 |
| 503 | `SERVICE_UNAVAILABLE` | MinIO 或 Redis 不可用 |

> **背后流程**：先 **完成 MinIO 上传**，再写入数据库并发 `parse_and_index` 任务。Python RAG Worker 从 **原文件** `minio_path` 解析与索引；Office 另在同目录写入 **`preview.pdf`** 供浏览器内联预览（不覆盖 `minio_path`）。

---

### GET `/api/v1/materials/{materialId}` — 材料详情（预览元数据）

**认证**：JWT（课程教师或已加入该课的学生）

**响应**：`200 OK`

```json
{
  "id": "material-uuid-...",
  "filename": "slides.pptx",
  "file_type": "pptx",
  "lesson_id": null,
  "status": "INDEXING",
  "preview_pdf_status": "PENDING",
  "indexed_chunk_count": 0,
  "created_at": "2026-05-11T09:00:00.000Z",
  "status_message": null
}
```

---

### GET `/api/v1/materials/{materialId}/content` — 流式下载 / 内联预览

**认证**：JWT（课程教师或已加入该课的学生）

**Query 参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `variant` | `string` | 可选。`original`：始终流式返回 **上传原文件**（`Content-Disposition: attachment`）。缺省：内联预览（`pdf`/`md`/`txt` 为原对象；Office 在 `preview_pdf_status === READY` 时返回 `preview.pdf`）。 |

**成功响应**：`200 OK`，`Content-Type` 与 `Content-Disposition` 依类型而定；正文为二进制流。

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 425 | `PREVIEW_NOT_READY` | Office 内联预览且 `preview.pdf` 尚未生成（`details.preview_pdf_status` 为 `PENDING` 等） |
| 400 | `VALIDATION_ERROR` | 不支持的 `file_type` 做内联预览 |

---

### GET `/api/v1/materials/{materialId}/chunks/{chunkId}` — 引用块文本（占位）

**认证**：JWT（课程成员）

**响应**：`501 NOT_IMPLEMENTED`（当前不暴露块级存储；客户端可仅用 SSE 引用标签 + 资料预览）。

---

### DELETE `/api/v1/materials/{materialId}` — 删除材料

**认证**：JWT（`TEACHER`，且为材料所属课程的创建者）

**路径参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `materialId` | `string (UUID)` | 材料 ID |

**响应**：`200 OK`

```json
{ "ok": true }
```

---

## M7 课程聊天（B3 SSE）

### POST `/api/v1/courses/{courseId}/chat` — 发送聊天消息（SSE 流式）

**描述**：学生向课程 AI 助手提问，返回 SSE 流式响应。必须先完成 Agent 身份绑定（JWT 中需含 `agent_user_id`）。

**认证**：JWT（`STUDENT` 或 `TEACHER`，且为课程成员；`agent_user_id` 必须存在）

**请求 Body**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message` | `string` | ✓ | 用户问题文本（trim 后不能为空） |
| `lesson_id` | `string (UUID)` | 否 | 限定 RAG 检索范围到指定课时 |

**请求示例**：

```http
POST /api/v1/courses/{courseId}/chat
Authorization: Bearer <token>
Content-Type: application/json

{
  "message": "请解释 TCP 三次握手的过程",
  "lesson_id": "lesson-uuid-..."
}
```

**响应**：`200 OK`（SSE 流）

```
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
Connection: keep-alive
```

**SSE 事件格式**：

每条事件均为 `data: <JSON>\n\n` 格式，JSON 结构如下：

| `type` | 字段 | 说明 |
|---|---|---|
| `text` | `content: string` | AI 回答的文本片段（流式推送） |
| `citation` | `chunk_id?: string`<br>`material_id?: string`<br>`source_label?: string` | 引用的知识库来源（在 `[DONE]` 前发送） |
| `done` | `tokens?: number`<br>`exec_time_ms?: number`<br>`error?: string` | 流结束标记，携带 token 统计和执行时间 |

**SSE 事件示例**：

```
data: {"type":"text","content":"TCP 三次握手是指"}

data: {"type":"text","content":"建立连接的过程，分为三个步骤..."}

data: {"type":"citation","chunk_id":"ck-001","material_id":"mat-uuid-...","source_label":"chapter1.pdf §2.3"}

data: {"type":"done","tokens":256,"exec_time_ms":1420}
```

**错误**（流开始前，以 JSON 错误格式返回）：

| 状态码 | code | 说明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | `message` 为空 |
| 400 | `AGENT_NOT_BOUND` | 未绑定 Agent 身份（需先完成 M3 绑定流程） |
| 404 | `NOT_FOUND` | `lesson_id` 不属于该课程 |
| 502 | `AGENT_CHAT_FAILED` | Agent 服务返回错误 |

> **隐私说明**：当 `User.qa_collection_enabled = false` 时，聊天正常进行但**不写入 `qa_logs`**。可通过 `PUT /api/v1/user` 修改此设置。

---

### GET `/api/v1/courses/{courseId}/chat/history` — 聊天历史记录

**描述**：获取学生在课程内的历史问答记录（仅含问题和答案，不含原始 token 统计）。

**认证**：JWT（学生查自己的记录；ADMIN 可传 `student_id` 查任意学生）

**Query 参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `limit` | `number` | 每页数量，1–100，默认 20 |
| `offset` | `number` | 分页偏移，默认 0 |
| `student_id` | `string (UUID)` | 仅 ADMIN 可用，查询指定学生记录 |

**响应**：`200 OK`

```json
{
  "logs": [
    {
      "id": "log-uuid-...",
      "question": "请解释 TCP 三次握手",
      "answer": "TCP 三次握手是...",
      "created_at": "2026-05-11T09:15:00.000Z",
      "hit_materials": ["mat-uuid-1", "mat-uuid-2"],
      "session_id": "session-uuid-..."
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 403 | `FORBIDDEN` | 教师不可访问原始问答记录（请使用分析接口） |

---

## M8 学习数据与分析

### GET `/api/v1/courses/{courseId}/analytics` — 课程数据面板（教师）

**描述**：获取课程维度的聚合学习数据，供教师分析。**不含学生原始 Q&A 内容**。

**认证**：JWT（`TEACHER` 且为课程创建者，或 `ADMIN`）

**Query 参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `start_date` | `string (ISO 8601)` | 起始时间，默认 7 天前 |
| `end_date` | `string (ISO 8601)` | 结束时间，默认当前时间 |

**响应**：`200 OK`

```json
{
  "total_questions": 128,
  "avg_response_time_ms": 1234,
  "top_questions": [
    {
      "question": "什么是 TCP 三次握手？",
      "count": 15,
      "avg_quality": null
    }
  ],
  "active_students": [
    {
      "student_id": "student-uuid-...",
      "name": "张三",
      "question_count": 23,
      "last_active": "2026-05-11T10:00:00.000Z"
    }
  ],
  "top_materials": [
    {
      "material_id": "mat-uuid-...",
      "title": "chapter1.pdf",
      "hit_count": 56
    }
  ],
  "weak_concepts": []
}
```

| 字段 | 说明 |
|---|---|
| `top_questions` | 高频问题 TOP 15，含平均质量分（待实现） |
| `active_students` | 活跃学生 TOP 20 |
| `top_materials` | 被命中最多的材料 TOP 15 |
| `weak_concepts` | 薄弱概念（预留字段，当前返回空数组） |

---

### GET `/api/v1/students/{studentId}/learning-progress` — 学生学习进度

**认证**：JWT（学生只能查自己；ADMIN 可查任意学生）

**路径参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `studentId` | `string (UUID)` | 学生用户 ID |

**响应**：`200 OK`

```json
{
  "student_id": "student-uuid-...",
  "total_questions": 45,
  "topics_covered": [],
  "weak_areas": [],
  "recent_activity": "2026-05-11T10:00:00.000Z",
  "engagement_score": 0.75
}
```

---

### GET `/api/v1/me/qa-logs` — 导出我的 QA 记录（GDPR）

**描述**：导出当前用户全部历史问答数据（GDPR 数据可携带性要求），最多返回 10,000 条。

**认证**：JWT（任意角色）

**响应**：`200 OK`

```json
{
  "user_id": "student-uuid-...",
  "qa_logs": [
    {
      "id": "log-uuid-...",
      "courseId": "course-uuid-...",
      "question": "...",
      "answer": "...",
      "createdAt": "2026-05-11T09:00:00.000Z",
      ...
    }
  ]
}
```

---

### DELETE `/api/v1/me/qa-logs` — 删除我的 QA 记录（GDPR 擦除）

**描述**：软删除当前用户所有 QA 记录（GDPR 被遗忘权）。记录仅标记 `deleted_at`，不物理删除。

**认证**：JWT（任意角色）

**响应**：`200 OK`

```json
{ "deleted_count": 45 }
```

---

## M9 内部接口（服务间调用）

> 以下接口仅供 EduAgent（Python 服务）调用，使用独立的 `X-Internal-Key` 认证，**不应在前端或外部使用**。

### GET `/api/v1/internal/course-rag-access` — 验证课程 RAG 访问权限

**描述**：EduAgent 在执行 RAG 检索前，调用此接口确认用户是否有权访问该课程的知识库。平台会自动将 `agent_user_id` 解析为 `platform_user_id` 再做权限校验。

**认证**：`X-Internal-Key: <INTERNAL_API_KEY>`

**Query 参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `course_id` | `string (UUID)` | ✓ | 课程 ID |
| `user_id` | `string` | ✓ | Agent 系统的用户 ID（`agent_user_id`） |

**请求示例**：

```http
GET /api/v1/internal/course-rag-access?course_id=xxx&user_id=agent-user-xyz
X-Internal-Key: internal-secret-key
```

**响应**：`200 OK`

```json
{ "access": true }
```

**错误**：

| 状态码 | code | 说明 |
|---|---|---|
| 401 | `UNAUTHORIZED` | `X-Internal-Key` 无效 |
| 503 | `SERVICE_UNAVAILABLE` | `INTERNAL_API_KEY` 未配置 |

---

## 枚举值参考

### UserRole

| 值 | 说明 |
|---|---|
| `STUDENT` | 学生 |
| `TEACHER` | 教师 |
| `ADMIN` | 管理员（不可通过注册创建） |

### CourseStatus

| 值 | 说明 |
|---|---|
| `DRAFT` | 草稿，学生无法加入 |
| `PUBLISHED` | 已发布，学生可加入 |
| `ARCHIVED` | 已归档 |

### MaterialStatus

| 值 | 说明 |
|---|---|
| `UPLOADED` | 已上传到 MinIO，等待处理 |
| `PARSING` | RAG Worker 正在解析 |
| `PARSED` | 解析完成，等待向量化 |
| `INDEXING` | 正在写入向量索引 |
| `READY` | 索引完成，可用于 RAG 检索 |
| `FAILED` | 处理失败 |

### CredentialStatus

| 值 | 说明 |
|---|---|
| `ACTIVE` | 有效，可用于绑定 |
| `USED` | 已完成绑定 |
| `EXPIRED` | 已过期 |
| `REVOKED` | 管理员撤销 |

---

## 接口快速索引

| 分组 | 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|---|
| **认证** | POST | `/api/v1/register` | 无 | 注册 |
| | POST | `/api/v1/login` | 无 | 登录 |
| | POST | `/api/v1/refresh` | 无 | 刷新 Token |
| | GET | `/api/v1/user` | JWT | 获取当前用户 |
| | PUT | `/api/v1/user` | JWT | 更新用户资料 |
| **凭证** | GET | `/api/v1/credentials` | JWT(STUDENT) | 我的凭证列表 |
| | GET | `/api/v1/admin/credentials` | JWT(ADMIN) | 管理员列举凭证 |
| | POST | `/api/v1/admin/credentials` | JWT(ADMIN) | 管理员创建凭证 |
| | DELETE | `/api/v1/admin/credentials/{id}` | JWT(ADMIN) | 撤销凭证 |
| **绑定** | POST | `/api/v1/bind/start` | API Key | 开始绑定 |
| | POST | `/api/v1/bind/complete` | API Key | 完成绑定 |
| **课程** | GET | `/api/v1/courses` | JWT | 课程列表 |
| | POST | `/api/v1/courses` | JWT(TEACHER) | 创建课程 |
| | GET | `/api/v1/courses/{id}` | JWT | 课程详情 |
| | PATCH | `/api/v1/courses/{id}` | JWT(TEACHER) | 更新课程 |
| | DELETE | `/api/v1/courses/{id}` | JWT(TEACHER) | 删除课程 |
| | POST | `/api/v1/courses/{id}/publish` | JWT(TEACHER) | 发布课程 |
| | POST | `/api/v1/courses/{id}/archive` | JWT(TEACHER) | 归档课程 |
| | POST | `/api/v1/courses/{id}/join` | JWT(STUDENT) | 加入课程 |
| **课时** | GET | `/api/v1/courses/{id}/lessons` | JWT | 课时列表 |
| | POST | `/api/v1/courses/{id}/lessons` | JWT(TEACHER) | 创建课时 |
| | PATCH | `/api/v1/courses/{id}/lessons/{lid}` | JWT(TEACHER) | 更新课时 |
| | DELETE | `/api/v1/courses/{id}/lessons/{lid}` | JWT(TEACHER) | 删除课时 |
| **材料** | GET | `/api/v1/courses/{id}/materials` | JWT | 材料列表 |
| | POST | `/api/v1/courses/{id}/materials` | JWT(TEACHER) | 上传材料 |
| | GET | `/api/v1/materials/{mid}` | JWT | 材料详情（含 `preview_pdf_status`） |
| | GET | `/api/v1/materials/{mid}/content` | JWT | 流式预览或 `?variant=original` 下载原文件 |
| | GET | `/api/v1/materials/{mid}/chunks/{cid}` | JWT | 占位（501） |
| | DELETE | `/api/v1/materials/{mid}` | JWT(TEACHER) | 删除材料 |
| **聊天** | POST | `/api/v1/courses/{id}/chat` | JWT(已绑定) | 聊天(SSE) |
| | GET | `/api/v1/courses/{id}/chat/history` | JWT | 聊天历史 |
| **分析** | GET | `/api/v1/courses/{id}/analytics` | JWT(TEACHER/ADMIN) | 课程数据面板 |
| | GET | `/api/v1/students/{sid}/learning-progress` | JWT | 学生进度 |
| | GET | `/api/v1/me/qa-logs` | JWT | 导出 QA 记录 |
| | DELETE | `/api/v1/me/qa-logs` | JWT | 删除 QA 记录 |
| **内部** | GET | `/api/v1/internal/course-rag-access` | Internal Key | RAG 访问鉴权 |
