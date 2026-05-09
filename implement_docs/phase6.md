# Phase 6：B1 平台基础：用户身份与凭证码绑定详细方案

## 目标与背景

A5 完成后，EduAgent 已经演变为一个完整的、支持多 channel 接入的服务。B1 标志着"教育平台"的正式启动。B1 的核心目标是建立**平台的身份管理与 Agent 绑定机制**，使教育平台能够：

1. **用户管理**：注册、登录、角色（teacher/student/admin）、个人信息。
2. **凭证码生成与绑定**：学生/教师通过凭证码在 Agent 侧绑定身份，建立平台用户 ↔ Agent channel 的映射。
3. **JWT 认证**：平台侧使用 JWT token 认证，与 Agent 的 channel identity mapping 协作。
4. **前端脚手架**：React + Ant Design Pro，提供登录、注册、用户中心、凭证码页面。

B1 完成后，教育平台应该具备以下特征：

- 学生可注册登录，生成凭证码（一次性或限时有效）。
- 通过凭证码，学生可在 Agent 侧绑定身份。
- 绑定成功后，平台通过 HTTP API 向 Agent 发送消息时，Agent 能准确识别用户身份。
- 教师可查看绑定学生列表、生成新凭证码、管理凭证码。

## 架构决策

### 决策 1：Java Spring Boot 项目结构

采用标准 Spring Boot 分层架构：

```
edu-platform/
├─ src/main/java/com/eduagent/
│  ├─ controller/          # HTTP 控制器
│  │  ├─ AuthController.java
│  │  ├─ UserController.java
│  │  ├─ CredentialController.java
│  │  └─ ...
│  ├─ service/             # 业务逻辑
│  │  ├─ AuthService.java
│  │  ├─ UserService.java
│  │  ├─ CredentialService.java
│  │  └─ ...
│  ├─ repository/          # 数据访问
│  │  ├─ UserRepository.java
│  │  ├─ CredentialRepository.java
│  │  └─ ...
│  ├─ entity/              # JPA 实体
│  │  ├─ User.java
│  │  ├─ Credential.java
│  │  └─ ...
│  ├─ dto/                 # 数据传输对象
│  │  ├─ LoginRequest.java
│  │  ├─ UserResponse.java
│  │  └─ ...
│  ├─ security/            # Spring Security 配置
│  │  ├─ JwtTokenProvider.java
│  │  ├─ SecurityConfig.java
│  │  └─ ...
│  ├─ util/                # 工具类
│  └─ EduPlatformApplication.java
├─ src/main/resources/
│  ├─ application.yml      # Spring 配置
│  ├─ application-dev.yml
│  ├─ application-prod.yml
│  └─ db/migration/        # Flyway/Liquibase 迁移脚本
├─ src/test/java/         # 单元测试
├─ pom.xml
└─ README.md
```

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

Token 生成与验证由 `JwtTokenProvider` 负责。

### 决策 4：凭证码的生成与有效期策略

- **代码格式**：8 位随机大小写字母数字（如 `aB3cD7eF`），提高复制正确率。
- **有效期**：默认 30 分钟，教师可生成长期凭证（如 7 天）。
- **一次性**：凭证码使用一次后自动标记为 USED，不可重复绑定。
- **撤销**：教师可主动撤销凭证码（状态改为 REVOKED），已绑定的不能撤销。
 - **频率限制（已确认）**：同一用户每小时生成凭证码有上限（例如 10 个/小时，具体数值可配置）。
 - **失败尝试限制（已确认）**：对 `bind-credential` 的失败尝试（code 不存在/过期/状态不对）进行计数并限流（例如同一用户或同一 IP 每小时最多 20 次失败），超限后短暂封禁或返回 429。

### 决策 5：凭证码绑定流程

```
1. 学生在平台生成凭证码 code=aB3cD7eF
2. 学生把凭证码告诉 Agent（通过 Agent HTTP API 或其他 channel）
3. Agent 调用 POST /api/v1/bind-credential，传递 {code, agent_user_id}
4. 平台验证：
   - code 存在且状态为 ACTIVE
   - code 未过期
   - code 的 user_id 与当前登录用户相同（或系统管理员绑定）
5. 平台创建 agent_identity_mapping 记录
6. 返回成功，Agent 获得 agent_user_id ↔ platform_user_id 的映射
7. 后续 Agent 可通过 API key 或其他方式标识为该 platform_user_id
```

### 决策 6：Front-end 框架与组件库

使用 React + Ant Design Pro：

```
edu-platform-web/
├─ src/
│  ├─ pages/
│  │  ├─ Login/
│  │  ├─ Register/
│  │  ├─ UserCenter/
│  │  ├─ CredentialManagement/
│  │  └─ ...
│  ├─ components/
│  ├─ services/          # API 调用
│  ├─ models/            # 数据模型
│  ├─ styles/
│  └─ App.tsx
├─ package.json
└─ README.md
```

### 决策 7：与 Agent 的集成方式

平台与 Agent 的通信通过 HTTP API：

```
Agent 初始化时：
1. 如果存在 credential code，调用 POST /api/v1/bind-credential
2. 平台返回 agent_user_id 与 channel_token
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

### 新建文件（Java 后端）

- [edu-platform/src/main/java/com/eduagent/entity/User.java](file:///edu-platform/src/main/java/com/eduagent/entity/User.java)
  职责：JPA User 实体。

- [edu-platform/src/main/java/com/eduagent/entity/Credential.java](file:///edu-platform/src/main/java/com/eduagent/entity/Credential.java)
  职责：JPA Credential 实体。

- [edu-platform/src/main/java/com/eduagent/entity/AgentIdentityMapping.java](file:///edu-platform/src/main/java/com/eduagent/entity/AgentIdentityMapping.java)
  职责：JPA AgentIdentityMapping 实体。

- [edu-platform/src/main/java/com/eduagent/repository/UserRepository.java](file:///edu-platform/src/main/java/com/eduagent/repository/UserRepository.java)
  职责：User 数据访问接口。

- [edu-platform/src/main/java/com/eduagent/repository/CredentialRepository.java](file:///edu-platform/src/main/java/com/eduagent/repository/CredentialRepository.java)
  职责：Credential 数据访问接口。

- [edu-platform/src/main/java/com/eduagent/repository/AgentIdentityMappingRepository.java](file:///edu-platform/src/main/java/com/eduagent/repository/AgentIdentityMappingRepository.java)
  职责：AgentIdentityMapping 数据访问接口。

- [edu-platform/src/main/java/com/eduagent/dto/LoginRequest.java](file:///edu-platform/src/main/java/com/eduagent/dto/LoginRequest.java)
  职责：登录请求 DTO。

- [edu-platform/src/main/java/com/eduagent/dto/LoginResponse.java](file:///edu-platform/src/main/java/com/eduagent/dto/LoginResponse.java)
  职责：登录响应 DTO（包含 JWT token）。

- [edu-platform/src/main/java/com/eduagent/dto/RegisterRequest.java](file:///edu-platform/src/main/java/com/eduagent/dto/RegisterRequest.java)
  职责：注册请求 DTO。

- [edu-platform/src/main/java/com/eduagent/dto/CredentialResponse.java](file:///edu-platform/src/main/java/com/eduagent/dto/CredentialResponse.java)
  职责：凭证码响应 DTO。

- [edu-platform/src/main/java/com/eduagent/security/JwtTokenProvider.java](file:///edu-platform/src/main/java/com/eduagent/security/JwtTokenProvider.java)
  职责：JWT 生成与验证。

- [edu-platform/src/main/java/com/eduagent/security/SecurityConfig.java](file:///edu-platform/src/main/java/com/eduagent/security/SecurityConfig.java)
  职责：Spring Security 配置，包含 JWT filter。

- [edu-platform/src/main/java/com/eduagent/controller/AuthController.java](file:///edu-platform/src/main/java/com/eduagent/controller/AuthController.java)
  职责：提供 POST /api/v1/login、POST /api/v1/register、POST /api/v1/bind-credential 端点。

- [edu-platform/src/main/java/com/eduagent/controller/UserController.java](file:///edu-platform/src/main/java/com/eduagent/controller/UserController.java)
  职责：提供 GET /api/v1/user、PUT /api/v1/user 端点。

- [edu-platform/src/main/java/com/eduagent/controller/CredentialController.java](file:///edu-platform/src/main/java/com/eduagent/controller/CredentialController.java)
  职责：提供凭证码管理端点（生成、列表、撤销、查询状态）。

- [edu-platform/src/main/java/com/eduagent/service/AuthService.java](file:///edu-platform/src/main/java/com/eduagent/service/AuthService.java)
  职责：认证业务逻辑（登录、注册、token 刷新）。

- [edu-platform/src/main/java/com/eduagent/service/UserService.java](file:///edu-platform/src/main/java/com/eduagent/service/UserService.java)
  职责：用户业务逻辑（查询、更新、列表）。

- [edu-platform/src/main/java/com/eduagent/service/CredentialService.java](file:///edu-platform/src/main/java/com/eduagent/service/CredentialService.java)
  职责：凭证码业务逻辑（生成、验证、绑定、撤销）。

- [edu-platform/src/main/resources/db/migration/V1__init.sql](file:///edu-platform/src/main/resources/db/migration/V1__init.sql)
  职责：Flyway 初始化脚本（建表）。

- [edu-platform/src/test/java/com/eduagent/service/AuthServiceTest.java](file:///edu-platform/src/test/java/com/eduagent/service/AuthServiceTest.java)
  职责：认证服务单元测试。

- [edu-platform/pom.xml](file:///edu-platform/pom.xml)
  职责：Maven 项目配置，依赖：Spring Boot、Spring Security、jjwt、postgresql、flyway 等。

### 新建文件（React 前端）

- [edu-platform-web/src/pages/Login/index.tsx](file:///edu-platform-web/src/pages/Login/index.tsx)
  职责：登录页面组件。

- [edu-platform-web/src/pages/Register/index.tsx](file:///edu-platform-web/src/pages/Register/index.tsx)
  职责：注册页面组件。

- [edu-platform-web/src/pages/UserCenter/index.tsx](file:///edu-platform-web/src/pages/UserCenter/index.tsx)
  职责：用户中心页面（展示个人信息、修改密码）。

- [edu-platform-web/src/pages/CredentialManagement/index.tsx](file:///edu-platform-web/src/pages/CredentialManagement/index.tsx)
  职责：凭证码管理页面（学生：查看凭证码；教师：生成、管理、撤销）。

- [edu-platform-web/src/services/authService.ts](file:///edu-platform-web/src/services/authService.ts)
  职责：API 调用（登录、注册、获取 token）。

- [edu-platform-web/src/services/credentialService.ts](file:///edu-platform-web/src/services/credentialService.ts)
  职责：API 调用（凭证码相关操作）。

- [edu-platform-web/src/models/user.ts](file:///edu-platform-web/src/models/user.ts)
  职责：用户数据模型。

- [edu-platform-web/src/models/credential.ts](file:///edu-platform-web/src/models/credential.ts)
  职责：凭证码数据模型。

- [edu-platform-web/package.json](file:///edu-platform-web/package.json)
  职责：npm 依赖配置。

- [edu-platform-web/README.md](file:///edu-platform-web/README.md)
  职责：前端项目说明。

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

**响应**：HTTP 201，返回创建的用户信息。

### 3. POST /api/v1/credentials (生成凭证码)

**请求**：
```json
{
  "expires_in_minutes": 30  // 可选，默认 30 分钟
}
```

**响应**：
```json
{
  "code": "aB3cD7eF",
  "expires_at": "2026-05-08T10:00:00Z",
  "status": "ACTIVE"
}
```

### 4. POST /api/v1/bind-credential

**请求**（由 Agent 端发起）：
```json
{
  "code": "aB3cD7eF",
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

**查询**：获取当前用户的所有凭证码。

**响应**：
```json
{
  "credentials": [
    {
      "id": "cred_uuid",
      "code": "aB3cD7eF",
      "status": "USED",
      "created_at": "...",
      "bound_at": "..."
    }
  ]
}
```

## 实施顺序

### 后端（Java）

1. 初始化 Spring Boot 项目结构。
2. 设计数据库 schema，执行 Flyway 迁移。
3. 实现 JPA 实体与 Repository。
4. 实现 JwtTokenProvider。
5. 实现 AuthService、UserService、CredentialService。
6. 实现 AuthController、UserController、CredentialController。
7. 配置 Spring Security。
8. 单元测试。
9. 集成测试（与 Agent HTTP API 的绑定流程）。

### 前端（React）

1. 项目脚手架与依赖配置。
2. 登录页面。
3. 注册页面。
4. 用户中心页面。
5. 凭证码管理页面。
6. 路由与认证 guard。
7. 错误处理与提示。
8. 样式与 UI 调整。

### 集成

1. 后端部署、前端构建。
2. Agent 集成 HTTP client，支持凭证码绑定。
3. E2E 测试：学生注册 → 生成凭证码 → Agent 绑定 → 发送消息 → Agent 识别用户。

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
- 所有密码正确加密存储，无明文。

### 凭证码

- 生成的凭证码格式正确（8 位随机字母数字）。
- 凭证码有有效期，过期自动失效。
- 凭证码一经使用标记为 USED，不可重复使用。
- 教师可查看、撤销凭证码。
- 同一用户在 1 小时内生成凭证码次数超过上限时，生成接口返回 429（或业务错误码），且不会创建新凭证码记录。
- 绑定凭证码在达到失败尝试次数上限后，绑定接口返回 429（或业务错误码），并进入短暂封禁窗口（时间可配置）。

### 绑定流程

- Agent 通过 `POST /api/v1/bind-credential` 绑定成功。
- 绑定成功后平台可通过 user_id 查询对应 agent 身份。
- Agent 后续请求能准确识别用户。

### JWT Token

- Token 包含用户信息与角色。
- Token 正确过期与刷新。
- 无效 token 被拒绝。

### 前端 UI

- 登录、注册页面可用，表单验证有效。
- 用户中心展示个人信息。
- 凭证码管理页面按角色展示不同功能。

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
