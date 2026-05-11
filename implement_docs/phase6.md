# Phase 6：B1 平台基础：用户身份与凭证码绑定详细方案

## 目标与背景

A5 完成后，EduAgent 已经演变为一个完整的、支持多 channel 接入的服务。B1 标志着"教育平台"的正式启动。B1 的核心目标是建立**平台的身份管理与 Agent 绑定机制**，使教育平台能够：

1. **用户管理**：注册、登录、角色（teacher/student/admin）、个人信息。
2. **凭证码生成与绑定**：学生（及需绑定的教师账号）通过凭证码在 Agent 侧绑定身份，建立平台用户 ↔ Agent channel 的映射；**凭证码的平台侧治理（为他人生成、撤销、按用户审计、长期有效期等）仅管理员**。
3. **JWT 认证**：平台侧使用 JWT token 认证，与 Agent 的 channel identity mapping 协作。
4. **Next.js 应用**：App Router 页面（登录、注册、用户中心、凭证码含**管理员治理**视图），UI 可选用 Ant Design 等组件库。

B1 完成后，教育平台应该具备以下特征：

- 学生可注册登录；**注册成功后由平台自动发放一条限时有效的凭证码**（明文仅在当次 API/注册成功提示中展示一次）。
- 通过凭证码，学生可在 Agent 侧绑定身份。
- 绑定成功后，平台通过 HTTP API 向 Agent 发送消息时，Agent 能准确识别用户身份。
- 教师可查看绑定学生列表；**生成新凭证码、撤销与全平台凭证治理由管理员负责**（教师不具备凭证管理权限）。

## 架构决策

### 决策 1：Next.js（App Router）项目结构

采用 **Next.js 全栈** 单仓：路由与页面在 `app/`，HTTP API 用 **Route Handlers**（`app/api/**/route.ts`），业务逻辑放在 `lib/`（可按 domain 分子目录），持久化用 **Prisma** + PostgreSQL。

```
edu-platform/                    # Next.js 根目录（名称可自定）
├─ app/
│  ├─ (auth)/login/page.tsx
│  ├─ (auth)/register/page.tsx
│  ├─ (app)/user/page.tsx              # 用户中心
│  ├─ (app)/credentials/page.tsx       # 凭证码（按角色分支 UI）
│  ├─ api/v1/login/route.ts
│  ├─ api/v1/register/route.ts
│  ├─ api/v1/bind/start/route.ts
│  ├─ api/v1/bind/complete/route.ts
│  ├─ api/v1/user/route.ts
│  ├─ api/v1/credentials/route.ts
│  ├─ api/v1/admin/credentials/route.ts
│  └─ ...
├─ components/               # 可复用 UI（Ant Design 包装等）
├─ lib/
│  ├─ db.ts                  # Prisma client 单例
│  ├─ auth.ts                # Auth.js / JWT 签发与校验
│  ├─ services/              # authService, userService, credentialService
│  └─ middleware-helpers.ts  # 角色校验（ADMIN 路由）
├─ prisma/
│  ├─ schema.prisma
│  └─ migrations/            # prisma migrate 生成
├─ middleware.ts              # 会话/JWT、受保护路由
├─ package.json
├─ .env.example
└─ README.md
```

**说明**：REST 路径与 B1 接口契约保持一致（`/api/v1/...`）；**管理员凭证治理**仅在 `app/api/v1/admin/**` 的 Route Handler 内校验 `ADMIN` 角色。

### 决策 2：数据库设计

使用 PostgreSQL，初期表结构如下：

```sql
-- 用户表
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('STUDENT', 'TEACHER', 'ADMIN') NOT NULL,
    real_name VARCHAR(255),
    avatar_url VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- 凭证码表
CREATE TABLE credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    code VARCHAR(32) UNIQUE NOT NULL,
    status ENUM('ACTIVE', 'USED', 'EXPIRED', 'REVOKED') NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,  -- NULL 表示不过期
    used_at TIMESTAMP,
    bound_agent_user_id VARCHAR(255),  -- Agent 侧的 user_id
    bound_at TIMESTAMP,
    metadata JSONB  -- 预留拓展字段
);

-- Agent channel 身份映射表
CREATE TABLE agent_identity_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_user_id UUID NOT NULL UNIQUE REFERENCES users(id),
    agent_user_id VARCHAR(255) NOT NULL UNIQUE,
    channel VARCHAR(50) NOT NULL,  -- "http", "websocket", ...
    bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP,
    metadata JSONB  -- 预留拓展字段
);

CREATE INDEX ON users(username);
CREATE INDEX ON credentials(code, status);
CREATE INDEX ON credentials(user_id, status);
```

### 决策 3：JWT Token 设计

JWT payload 包含：

```json
{
  "sub": "platform_user_id",
  "username": "john_doe",
  "role": "STUDENT",
  "agent_user_id": "agent_12345",  // 已绑定时
  "iat": 1620000000,
  "exp": 1620003600,
  "iss": "edu-platform"
}
```

Token 生成与验证由 **`lib/auth.ts`（如 `jose`）或 Auth.js callbacks** 负责；Route Handler 中统一读取 `Authorization` 并解析 `role`。

### 决策 4：凭证码的生成与有效期策略

- **代码格式**：8 位随机大小写字母数字（如 `aB3cD7eF`），提高复制正确率。
- **有效期**：默认 30 分钟；**超过默认上限的长期凭证（如 7 天）仅管理员可为指定用户生成**（学生注册时发放的凭证受 `SELF_CREDENTIAL_MAX_EXPIRES_MINUTES` 等配置约束；管理员代发另受接口校验）。
- **一次性**：凭证码使用一次后自动标记为 USED，不可重复绑定。
- **撤销**：**仅管理员**可主动撤销凭证码（状态改为 REVOKED），已绑定的不能撤销。
 - **频率限制（已确认）**：同一用户每小时生成凭证码有上限（例如 10 个/小时，具体数值可配置）。
 - **失败尝试限制（已确认）**：对绑定流程（`bind/start` / `bind/complete`）的失败尝试按 **IP** 计数并限流（例如每小时最多 20 次失败），超限后 **Redis 封禁键**或（无 Redis 时）Postgres 记录 + 429；详见实现 `rateLimit.ts`。

### 决策 5：凭证码绑定流程

**最终实现**：Agent 使用 **`X-Platform-Bind-Key`** 调用两步 HTTP；**不**依赖学生浏览器 JWT。

```
1. 学生注册时平台发放凭证码（或管理员代发），学生将 code 提供给 Agent
2. Agent：POST /api/v1/bind/start，body { code } → 获得 bind_challenge_token（Redis 短期存储 code 摘要）
3. Agent：POST /api/v1/bind/complete，body { bind_challenge_token, agent_user_id, channel }
4. 平台在事务内校验 challenge、将 credential 置 USED、写入 agent_identity_mapping
5. 返回 channel_token 等
```

### 决策 6：UI 与客户端数据

- 页面与布局在 **`app/`** 下组织；需浏览器交互的表单、列表使用 **Client Component**（`"use client"`）。
- **Server Actions** 或 **`fetch` 调用同源的 `/api/v1/**`** 完成登录、凭证列表等；避免在客户端暴露服务密钥。
- 组件库可选 **Ant Design**（`@ant-design/nextjs-registry` 等与 App Router 的配合按官方文档配置）。

### 决策 7：与 Agent 的集成方式

平台与 Agent 的通信通过 HTTP API：

```
Agent 初始化时：
1. 如有 credential code：依次调用 POST /api/v1/bind/start 与 POST /api/v1/bind/complete
2. 平台返回 channel_token 等
3. Agent 后续通过 header 携带 token 标识身份

平台调用 Agent 时：
1. POST /v1/chat/completions，header 中包含 X-Platform-User-Id, X-Platform-Session-Id
2. Agent 通过 Agent Identity Mapping 验证并建立 session
```

### 决策 8：安全考虑

- **密码**：使用 bcrypt 加密存储。
- **Token 失效**：短期 token（15 分钟），需要 refresh token 延期。
- **HTTPS**：生产环境强制 HTTPS。
- **CORS**：配置合理的 CORS 策略。
- **凭证码安全**：凭证码仅在生成时明文显示一次，之后只存储哈希值（可选，取决于安全需求）。

## 文件清单

### 新建文件（Next.js 全栈，路径相对仓库根目录 `edu-platform/`）

- [edu-platform/prisma/schema.prisma](file:///edu-platform/prisma/schema.prisma)
  职责：`User`、`Credential`、`AgentIdentityMapping` 等模型与枚举；与本文「决策 2」表结构一致。

- [edu-platform/prisma/migrations/...](file:///edu-platform/prisma/migrations)
  职责：**Prisma Migrate** 生成的建表迁移（替代 Flyway）。

- [edu-platform/lib/db.ts](file:///edu-platform/lib/db.ts)
  职责：Prisma Client 单例（避免开发环境热重载多实例）。

- [edu-platform/lib/auth.ts](file:///edu-platform/lib/auth.ts)
  职责：密码哈希（bcrypt/argon2）、JWT 签发与校验，或与 **Auth.js** 集成。

- [edu-platform/lib/services/authService.ts](file:///edu-platform/lib/services/authService.ts)
  职责：登录、注册（含学生凭证事务发放）、refresh token（单次消费事务化）。

- [edu-platform/lib/services/userService.ts](file:///edu-platform/lib/services/userService.ts)
  职责：用户查询与更新。

- [edu-platform/lib/services/credentialService.ts](file:///edu-platform/lib/services/credentialService.ts)
  职责：凭证生成、列表、哈希存储、绑定、限流；**代他人生成与撤销**仅在校验 `ADMIN` 后执行。

- [edu-platform/app/api/v1/login/route.ts](file:///edu-platform/app/api/v1/login/route.ts)
  职责：`POST /api/v1/login`。

- [edu-platform/app/api/v1/register/route.ts](file:///edu-platform/app/api/v1/register/route.ts)
  职责：`POST /api/v1/register`。

- [edu-platform/app/api/v1/bind/start/route.ts](file:///edu-platform/app/api/v1/bind/start/route.ts) / [complete/route.ts](file:///edu-platform/app/api/v1/bind/complete/route.ts)
  职责：Agent 绑定两步接口；`X-Platform-Bind-Key`；依赖 Redis challenge。

- [edu-platform/app/api/v1/user/route.ts](file:///edu-platform/app/api/v1/user/route.ts)
  职责：`GET` / `PUT /api/v1/user`。

- [edu-platform/app/api/v1/credentials/route.ts](file:///edu-platform/app/api/v1/credentials/route.ts)
  职责：`GET` 本人列表（仅学生）；`POST` 关闭（403）。

- [edu-platform/app/api/v1/admin/credentials/route.ts](file:///edu-platform/app/api/v1/admin/credentials/route.ts)
  职责：管理员 `POST` / `GET /api/v1/admin/credentials`（列表无明文 code）。

- [edu-platform/app/api/v1/admin/credentials/[id]/route.ts](file:///edu-platform/app/api/v1/admin/credentials/[id]/route.ts)
  职责：`DELETE /api/v1/admin/credentials/{id}` 撤销。

- [edu-platform/middleware.ts](file:///edu-platform/middleware.ts)
  职责：保护需登录的 `app/(app)/**`；**不**替代 admin API 内的二次角色校验。

- [edu-platform/app/(auth)/login/page.tsx](file:///edu-platform/app/(auth)/login/page.tsx)
  职责：登录页（Client 表单 + Server Action 或 fetch）。

- [edu-platform/app/(auth)/register/page.tsx](file:///edu-platform/app/(auth)/register/page.tsx)
  职责：注册页。

- [edu-platform/app/(app)/user/page.tsx](file:///edu-platform/app/(app)/user/page.tsx)
  职责：用户中心。

- [edu-platform/app/(app)/credentials/page.tsx](file:///edu-platform/app/(app)/credentials/page.tsx)
  职责：凭证码页（按角色分支：学生/教师自助；**管理员**治理入口）。

- [edu-platform/package.json](file:///edu-platform/package.json)
  职责：`next`、`react`、`prisma`、`@prisma/client`、`bcrypt` 或 `argon2`、`jose`（或 `next-auth`）等依赖。

- [edu-platform/README.md](file:///edu-platform/README.md)
  职责：本地开发（`prisma migrate dev`）、环境变量说明。

- [edu-platform/__tests__/authService.test.ts](file:///edu-platform/__tests__/authService.test.ts)（或 `vitest` / `jest` 约定目录）
  职责：认证与凭证服务单元测试。

## 接口契约

### 1. POST /api/v1/login

**请求**：
```json
{
  "username": "john_doe",
  "password": "password123"
}
```

**响应**（成功）：
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "id": "user_uuid",
    "username": "john_doe",
    "role": "STUDENT",
    "real_name": "John Doe"
  }
}
```

### 2. POST /api/v1/register

**请求**：
```json
{
  "username": "john_doe",
  "email": "john@example.com",
  "password": "password123",
  "role": "STUDENT"
}
```

**响应**：HTTP 201，返回 `{ "user": { ... } }`；当 `role` 为 `STUDENT` 时另含 **`credential`** 对象（与旧自助接口字段一致：`code`、`expires_at`、`status`），**明文 code 仅此次响应可见**。

### 3. POST /api/v1/credentials

**已关闭自助生成**：`POST` 返回 **403**。学生凭证由**注册发放**或**管理员** `POST /api/v1/admin/credentials` 创建。

### 3a. GET /api/v1/credentials

**权限**：仅 **`STUDENT`** 可查询本人凭证列表；`TEACHER` 与 **`ADMIN`** 返回 **403**（管理员仅通过 `admin/credentials` 治理，不通过本接口查看「我的凭证」）。

### 3b. POST /api/v1/admin/credentials（管理员为指定用户生成）

**权限**：仅 `ROLE_ADMIN`。

**请求**：
```json
{
  "user_id": "target_platform_user_uuid",
  "expires_in_minutes": 10080  // 例如 7 天，可高于自助上限
}
```

**响应**：与注册发放字段一致（含明文 `code` 一次），且响应体或审计日志中关联 `user_id`。

### 4. Agent 绑定（两步 HTTP + Redis challenge）

**鉴权**：两步接口均要求请求头 **`X-Platform-Bind-Key`**，与 `BIND_CREDENTIAL_API_KEY` 做常量时间比对。

**依赖**：需配置 **`REDIS_URL`**，用于短期 `bind_challenge_token` 与（推荐）绑定失败限流/封禁。

#### 4a. POST /api/v1/bind/start

**请求**：
```json
{ "code": "aB3cD7eF" }
```

**响应**：`{ "bind_challenge_token": "<opaque>" }` — 校验 code 为 ACTIVE 且未过期，**不**消耗凭证；在 Redis 写入与 `code` 对应的挑战，TTL 可配置（默认见 `BIND_CHALLENGE_TTL_SEC`）。

#### 4b. POST /api/v1/bind/complete

**请求**：
```json
{
  "bind_challenge_token": "<from start>",
  "agent_user_id": "agent_uuid_12345",
  "channel": "http"
}
```

**响应**：
```json
{
  "success": true,
  "platform_user_id": "platform_uuid",
  "channel_token": "auth_token_for_channel"
}
```

### 5. GET /api/v1/credentials

见 **3a**。

### 5b. GET /api/v1/admin/credentials

**权限**：仅 `ROLE_ADMIN`。

**查询**：可选 `user_id`、`status` 等筛选；列表接口**不返回**明文 code（仅 id、status、时间、目标 user_id 等元数据）；新建凭证时明文仅在 `POST /api/v1/admin/credentials` 当次响应返回一次。

**响应**（示例字段，无明文 code）：
```json
{
  "credentials": [
    {
      "id": "cred_uuid",
      "user_id": "platform_user_uuid",
      "status": "USED",
      "created_at": "...",
      "bound_at": "..."
    }
  ]
}
```

### 6. DELETE /api/v1/admin/credentials/{id}（撤销）

**权限**：仅 `ROLE_ADMIN`。将未使用且未绑定完成的凭证置为 `REVOKED`（已 `USED` 且已绑定的不回滚映射，与决策 4 一致）。

## 实施顺序

### Next.js 应用（API + 页面）

1. `create-next-app`（App Router、TypeScript），接入 **Prisma** 与 PostgreSQL。
2. 在 `schema.prisma` 中建模并执行 **`prisma migrate`**（对齐决策 2）。
3. 实现 `lib/services/*`（Auth / User / Credential）与 **`lib/auth.ts`** JWT 或 Auth.js。
4. 实现 **`app/api/v1/**` Route Handlers**（login、register、user、credentials、admin、credentials、bind/start、bind/complete）。
5. **`middleware.ts`** 与会话/ Cookie 或 Bearer 策略；admin 路由内强制 `ADMIN`。
6. 实现 **`app/(auth)/*`、`app/(app)/*`** 页面与 Client 组件；Ant Design 按需接入。
7. 单元测试（Vitest/Jest）+ 与 Agent 的绑定流程集成测试。

### 集成

1. `next build` 与生产环境部署（Node 适配器或 Docker 内 `node server.js`）。
2. Agent 侧 HTTP client 调用平台 `POST /api/v1/bind/start` 与 `POST /api/v1/bind/complete`（及 `REDIS_URL` 就绪）。
3. E2E：学生注册 → 获得平台发放凭证码 → Agent 两步绑定 → 发送消息 → Agent 识别用户。

## 注意事项

### 1. 密码强度要求

建议：最少 8 字符，包含大小写字母、数字、特殊字符中的至少 3 类。

### 2. 凭证码的安全性

当前方案中凭证码在生成后以明文返回给用户。若要提升安全性，可改为：

- 凭证码在数据库中存储哈希值。
- 生成时返回原始码给用户（仅一次）。
- 验证时比较哈希值。

> 已确认：B1 直接落地哈希存储（凭证码只在生成瞬间明文返回一次），不再延后。

补充（已确认）：配合频率限制与失败尝试限制，避免“撞库/枚举凭证码”与“刷凭证码”。

### 3. 邮箱验证

当前方案不包含邮箱验证流程。建议后续增加：

- 注册时发送验证邮件。
- 用户点击邮件中的链接后才能激活账户。

### 4. 凭证码作为"一时通行证"的安全隐患

如果凭证码被截获（如通过日志或网络嗅探），任何人都可以用它绑定 Agent。建议：

- HTTPS 加密传输。
- 凭证码一经使用立即失效。
- 提供撤销机制。
- 后期可加 MFA（多因素认证）。

## 验收标准

### 用户管理

- 学生可注册、登录、修改个人信息。
- 教师可注册、登录、查看绑定学生。
- **管理员**可注册、登录，并完成凭证码治理相关操作（见上「凭证码」小节）。
- 所有密码正确加密存储，无明文。

### 凭证码

- 生成的凭证码格式正确（8 位随机字母数字）。
- 凭证码有有效期，过期自动失效。
- 凭证码一经使用标记为 USED，不可重复使用。
- **管理员**可查看（按筛选条件）、撤销凭证码；**教师**不具备撤销或代管他人凭证的权限。
- 同一用户在 1 小时内生成凭证码次数超过上限时，生成接口返回 429（或业务错误码），且不会创建新凭证码记录。
- 绑定凭证码在达到失败尝试次数上限后，绑定接口返回 429（或业务错误码），并进入短暂封禁窗口（时间可配置）。

### 绑定流程

- Agent 先 `POST /api/v1/bind/start` 提交 `code`，再 `POST /api/v1/bind/complete` 提交 `bind_challenge_token` 与 `agent_user_id`、`channel` 完成绑定。
- 绑定成功后平台可通过 user_id 查询对应 agent 身份。
- Agent 后续请求能准确识别用户。

### JWT Token

- Token 包含用户信息与角色。
- Token 正确过期与刷新。
- 无效 token 被拒绝。

### 前端 UI

- 登录、注册页面可用，表单验证有效。
- 用户中心展示个人信息。
- 凭证码页面按角色展示不同功能：**管理员**具备治理入口；**教师**无凭证入口（无导航、无 API、访问 `/credentials` 重定向至用户中心）。

## 本阶段不做

- 不做 OAuth 第三方登录（如 GitHub、Google）。
- 不做 LDAP 或 AD 集成（企业用户管理）。
- 不做复杂的权限模型（细粒度 RBAC）。
- 不做消息通知系统（邮件、短信通知）。
- 不做用户审核/审批流程。

## 确认的开放点

### 1. 角色权限

当前方案定义了 STUDENT、TEACHER、ADMIN 三个角色。是否需要更多细分角色（如 ASSISTANT、FACILITATOR）？

> 初期三个角色足够。后期根据业务需求增加。

### 2. 凭证码生成频率限制

是否需要限制用户生成凭证码的频率（如"每小时最多 10 个"）？

> 已确认：B1 直接加入“同用户每小时生成上限”。

### 4. 绑定失败尝试次数限制

是否需要限制用户（或 IP）在绑定凭证码时的失败尝试次数（防止枚举 code）？

> 已确认：B1 直接加入失败尝试次数限制与限流（返回 429/封禁窗口可配置）。

### 3. 与 Agent 的通信协议

当前方案中 Agent 通过 HTTP REST API 与平台通信。是否考虑使用 gRPC 或 GraphQL？

> 先用 HTTP REST（标准、简单），后期性能瓶颈再升级。
