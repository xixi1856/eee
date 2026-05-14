# Phase 1 — 架构简化

## 目标
删除 bind/credential 系统、非 HTTP 渠道，统一用户身份，
改由平台请求头注入课程访问权限，不再需要 Agent 回调平台验权。

## 执行契约

### 1. edu-platform 侧

#### 1.1 删除 API 路由目录
```
app/api/v1/bind/            ← 整目录删除
app/api/v1/bind-credential/ ← 整目录删除
app/api/v1/credentials/     ← 整目录删除
app/api/v1/internal/course-rag-access/   ← 删除
app/api/v1/internal/enrolled-courses-rag/ ← 删除
```

#### 1.2 删除测试文件
```
__tests__/bind-credential-key.test.ts  ← 删除
__tests__/bind-refresh.test.ts         ← 删除
__tests__/credential-code.test.ts      ← 删除
__tests__/internal-course-rag-access.test.ts ← 删除
__tests__/enrolled-courses-rag.test.ts  ← 删除（逻辑移至 header 注入，新逻辑需新测试）
```

#### 1.3 删除 lib 工具文件
```
lib/bind-challenge.ts             ← 删除
lib/bind-credential-key.ts        ← 删除
lib/credential-code.ts            ← 删除
lib/agent-not-bound-error.ts      ← 删除（3 个 chat 路由去掉 agent_user_id 检查后无引用）
lib/services/credentialService.ts ← 删除（依赖 Credential 表，表删除后无法使用）
```

#### 1.4 修改 `lib/agentClient.ts`
- 删除 `X-Platform-User-Id` header（旧语义：agentUserId，是 agent 侧的身份）
- 新增 header `X-Platform-User-Id`：直接使用平台 JWT 的 `userId`（UUID）
- 新增 header `X-Platform-Accessible-Course-Ids`：逗号分隔的课程 UUID 列表
  - 数据来源：调用 `getAccessibleCourseIds(userId)` 函数（见 1.5）
- 删除 `agentUserId` 参数，改为 `userId: string`

函数签名变更：
```typescript
// 旧
postChatCompletionsStream(agentUserId, courseId, lessonId, messages, ...)
// 新
postChatCompletionsStream(userId, courseId, lessonId, messages, ...)
// headers 新增
"X-Platform-Accessible-Course-Ids": accessibleCourseIds.join(",")
```

#### 1.5 新增 `lib/course-access-injector.ts`
```typescript
// 直接复用 lib/course-rag-courses.ts 中已有的 listCourseIdsForRag(userId, role)
// 避免重复实现相同的 enrollment 查询逻辑
import { listCourseIdsForRag } from "@/lib/course-rag-courses";

export async function getAccessibleCourseIds(userId: string, role: UserRole): Promise<string[]> {
  return listCourseIdsForRag(userId, role);
}
```

> `lib/course-rag-courses.ts` 中的 `listCourseIdsForRag(platformUserId, role)` 已实现完整的
> 学生 / 教师 / 管理员三种角色的课程查询逻辑，直接调用即可，不要重写。

#### 1.6 Prisma Migration — 删除 3 张表
```prisma
// 删除以下 model（生成 migration SQL）：
// - Credential
// - AgentIdentityMapping
// - CredentialBindAttempt
```

执行：
```bash
npx prisma migrate dev --name remove_bind_credential_system
```

#### 1.7 修改各 chat 路由 — 删除 `agent_user_id` 检查

受影响的三个路由：

**`app/api/v1/courses/[courseId]/chat/route.ts`**
- 第 9 行：删除 `import { agentNotBoundError } from "@/lib/agent-not-bound-error"`
- 第 21 行：删除 `if (!auth.agent_user_id) return agentNotBoundError()` 整行
- 第 54 行：`agentUserId: auth.agent_user_id` → `userId: auth.sub`

**`app/api/v1/qa-center/chat/route.ts`**
- 第 6 行：删除 `agentNotBoundError` import
- 第 14 行：删除 `if (!auth.agent_user_id)` 检查整行
- 第 51 行：`agentUserId: auth.agent_user_id` → `userId: auth.sub`

**`app/api/v1/me/chat-threads/route.ts`**
- 删除 `agentNotBoundError` import
- 删除 `if (!auth.agent_user_id)` 检查整行
- `createEmptyGlobalThread(auth.sub, auth.agent_user_id)` → `createEmptyGlobalThread(auth.sub)`

**`app/api/v1/user/route.ts`**
- 删除响应体中的 `agent_identity_bound: !!ctx.agent_user_id` 字段

#### 1.8 修改 `lib/services/chatService.ts`

`getOrCreateCourseChatSession(courseId, platformStudentId, agentUserId)` 签名变更：
```typescript
// 旧
export async function getOrCreateCourseChatSession(
  courseId: string,
  platformStudentId: string,
  agentUserId: string,       // ← 删除此参数
)

// 新
export async function getOrCreateCourseChatSession(
  courseId: string,
  platformStudentId: string, // 直接作为 agent session 的学生标识
)
```

内部的 `createAgentSession(agentUserId, ...)` 调用改为 `createAgentSession(platformStudentId, ...)`。

`courseChatSseResponse` 参数对象：删除 `agentUserId` 字段，改为 `userId: string`。
`getOrCreateQaCenterAgentSession` / `qaCenterSseResponse` 同样处理。

> `CourseChatSession.studentId` 说明：当前该字段通过 `agentUserId` 间接赋值；
> 改造后直接使用平台 JWT 的 `userId`（即 `User.id` UUID），语义更清晰。

#### 1.9 更新 `prisma/seed-mock-students.ts`

文件第 46 行硬编码 `const agentUserId = "mock-agent-student-${idx}"`，
第 73、77 行将其写入 `User` 模型。

Phase 1 删除 `AgentIdentityMapping` 表后，`agentUserId` 字段也从 `User` schema 移除：
- 删除第 46 行 `agentUserId` 赋值
- 删除第 73、77 行 `agentUserId` 字段传参
- 执行 `npx prisma migrate dev` 后确认 seed 脚本可正常运行

### 2. Python Agent 侧

#### 2.1 删除 channel 文件
```
src/edu_agent/channels/cli.py
src/edu_agent/channels/feishu.py
src/edu_agent/channels/weixin.py
src/edu_agent/channels/weixin/      ← 整目录
src/edu_agent/channels/websocket.py ← 删除（edu-platform 侧聊天全走 HTTP SSE，无 WS 调用）
```

同步清理 `src/edu_agent/api/server.py`：
- 删除第 334–338 行的 `/v1/ws` 路由及其 `websocket_chat_loop` 调用
- 删除对应的 `WebSocket` import 语句

同步清理 `src/edu_agent/config.py`：
- 删除 `websocket_enabled: bool = True` 字段（约第 150 行）

#### 2.2 简化 `channels/registry.py`
删除所有 Weixin / Feishu 相关 import 和注册逻辑，仅保留 HTTPChannelAdapter 注册。

新版本（约 15 行）：
```python
from edu_agent.channels.http import HTTPChannelAdapter
from edu_agent.config import EduSettings
from edu_agent.runner.gateway import Gateway
from edu_agent.sessions.store import SessionStore

def register_channel_adapters(gateway, *, settings, paths, session_store, host, port):
    http = HTTPChannelAdapter(gateway, session_store, host=host, port=port)
    gateway.register_adapter(http)
```

#### 2.3 删除 CLI bind 相关命令
文件：`src/edu_agent/cli.py`、`src/edu_agent/cli_preflight.py`
- 删除 `edu bind` 命令组及其所有子命令
- 删除 `edu channels` 命令组
- 保留 `edu chat`（作为开发调试用，或在 Phase 3 完成后整体删除 CLI）

#### 2.4 修改 `tools/rag.py` — 课程权限来源
**旧**：`_sync_verify_and_query_course()` 通过 HTTP 调用 `/api/v1/internal/course-rag-access`
**新**：从 `TurnRuntimeContext` 读取 `accessible_course_ids`（由 HTTP channel 从请求头解析后注入）

变更点：
1. `HTTPChannelAdapter.handle_request()` 解析 `X-Platform-Accessible-Course-Ids` header → 存入 `TurnRuntimeContext.accessible_course_ids: list[str]`
2. `_sync_verify_and_query_course(course_id, user_id)` 改为：
   ```python
   ctx = turn_context.get()
   if course_id not in ctx.accessible_course_ids:
       return {"access": False}
   ```
3. `_sync_list_enrolled_course_ids()` 改为直接返回 `ctx.accessible_course_ids`
4. 删除 `EDU_PLATFORM_INTERNAL_API_KEY` 和 `EDU_PLATFORM_BASE_URL` 环境变量依赖

#### 2.5 删除 `auth/` 目录下的 bind 鉴权逻辑
明确删除以下两个文件：
```
src/edu_agent/auth/bind_client.py   ← 删除（向平台发起 bind 请求的客户端）
src/edu_agent/auth/token_store.py   ← 删除（存储 agent 侧 token 的缓存）
```
保留 `auth/checker.py` 和 `auth/models.py`（仍用于 HTTP channel 的 JWT 校验）。
检查 `auth/__init__.py` 删除对上述两个文件的 import。

### 3. 环境变量清理
删除以下不再需要的环境变量（`.env.example` + `docker-compose.yml`）：
```
BIND_CREDENTIAL_API_KEY       ← 删除
EDU_PLATFORM_INTERNAL_API_KEY ← 删除（被 header 注入替代）
EDU_AGENT_API_KEY             ← 保留（HTTP channel 仍需鉴权）
EDU_PLATFORM_BASE_URL         ← 删除
```

## 执行后验证方法

### 验证 1：数据库迁移正确
```bash
cd edu-platform
npx prisma migrate dev
npx prisma db pull  # 确认 schema 与 DB 一致
# 确认 credentials / agent_identity_mappings / credential_bind_attempts 表不存在
```

### 验证 2：单元测试
```bash
cd edu-platform
npx vitest run
# 预期：bind-credential-key / bind-refresh / credential-code / internal-course-rag-access 测试文件已删除
# 预期：其余 11 个测试文件全部通过
```

### 验证 3：agentClient header 注入
在 `__tests__/` 新增 `course-access-injector.test.ts`：
- mock CourseEnrollment DB 查询
- 断言 `getAccessibleCourseIds` 返回正确 course_id 列表

### 验证 4：聊天 E2E 流程（手动）
1. 启动 edu-platform（`npm run dev`）
2. 启动 Python agent（`uv run edu-gateway`）
3. 登录为学生 → 进入课程页面 → 发送一条消息
4. 确认 Agent 收到 `X-Platform-Accessible-Course-Ids` header（在 agent 日志中）
5. 确认 `knowledge_query(sources="course")` 不再发起 HTTP 回调，改为直接验证 context

### 验证 5：Python agent 渠道清理
```bash
uv run edu --help
# 预期：无 bind / channels 子命令
# 预期：仅显示 chat 及其他保留命令
```

## 回滚方案
Phase 1 纯删减，无需回滚。Git 历史保留所有删除内容，需要时可 cherry-pick 恢复。