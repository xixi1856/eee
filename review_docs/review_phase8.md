# Phase 8（B3）系统级代码审计报告

**基准**：[implement_docs/phase8.md](../implement_docs/phase8.md) 及 Phase 7 文档中关于 `knowledge_query` 与 `course_id` 运行时节点的约定。  
**审计对象**：`edu-platform/`（Next.js、Prisma、API）、`src/edu_agent/`（Agent HTTP、工具、流式 SSE）、`src/edu_agent/tools/rag.py`（课程 RAG 内部校验链）。  
**假设**：生产环境部署；攻击者可能掌握部分凭证或利用实现缺陷横向越权。

---

## A. 架构级问题（最重要）

### A1. 课程 RAG 内部校验链：`user_id` 语义错配（Critical，已修复）

**现象**：EduAgent `knowledge_query` 课程腿通过 `_sync_verify_and_query_course` 调用平台的 `GET /api/v1/internal/course-rag-access`，查询参数 `user_id` 取自运行时 `ctx.user_id`，即 **Agent 会话用户 id**（`agent_user_id`），见 [rag.py](src/edu_agent/tools/rag.py) 中 `params={"course_id": course_id, "user_id": user_id}`。

**原问题**：平台 [course-rag-access/route.ts](edu-platform/app/api/v1/internal/course-rag-access/route.ts) 曾将该值 **直接** 传入 [hasCourseRagAccess](edu-platform/lib/course-access.ts)（期望平台 `users.id`），与 [AgentIdentityMapping](edu-platform/prisma/schema.prisma) 双字段模型不一致。

**修复**：内部路由在调用 `hasCourseRagAccess` 前，通过 `AgentIdentityMapping.findUnique({ where: { agentUserId } })` 解析为 `platformUserId`；无映射时回传原 `user_id`（兼容 agent id 等于平台 UUID 的旧数据）。单测见 [internal-course-rag-access.test.ts](edu-platform/__tests__/internal-course-rag-access.test.ts)。

**残余风险**：仍见 **B-H2**（会话与 header 课程 id 的纵深绑定）。

---

### A2. 管理员按 `student_id` 审计聊天历史被 `getCourseIfMember` 误拦（High，已修复）

Phase 8 规定：管理员可在审计场景下按 `student_id` 查看 `qa_logs` 明细。

**原问题**：`chat/history` 在审计分支生效前即调用 `getCourseIfMember`；[getCourseIfMember](edu-platform/lib/course-access.ts) 仅放行 **该课教师** 或 **选课学生**，**ADMIN** 非成员时 **403**。

**修复**：当存在 `student_id` 查询参数时，改为 `requireAdmin` + 校验课程存在，**不再**要求管理员选课或任课；非审计路径仍走 `getCourseIfMember` 与教师禁止列表逻辑。见当前 [chat/history/route.ts](edu-platform/app/api/v1/courses/[courseId]/chat/history/route.ts)。

---

### A3. 平台 ↔ Agent 身份双轨与文档命名（Medium）

- 平台持久化 QA 使用 JWT `sub`（平台用户 UUID）与 [CourseChatSession](edu-platform/prisma/schema.prisma) 绑定 `agent_session_id`。
- Agent HTTP 使用 query `user_id` = `agent_user_id`（见 [agentClient.ts](edu-platform/lib/agentClient.ts)）；[server.py](src/edu_agent/api/server.py) 要求 `X-Platform-User-Id` 与 query `user_id` **一致**，语义实为 **Agent 用户 id**，与 Phase 8 示例中的「platform_user_uuid」字面 **不一致**（实现内部自洽，但文档与集成方易误解）。

---

### A4. 多轮会话与课程隔离（符合设计 / Low 说明）

- **会话隔离**：`CourseChatSession` 上 `@@unique([courseId, studentId])`（见 schema），同一学生每课单 Agent 会话，**无跨课共享**。
- **上下文**：Next 仅向 Agent POST **单条** user message（[agentClient.ts](edu-platform/lib/agentClient.ts)）；Agent [agent.py](src/edu_agent/agent.py) 在 `run_turn_stream` 中 append 并持久化消息，依赖 **同 `session_id`** 的会话存储维持多轮。与 Phase 8「二选一固定为 Agent 侧持久化 + 增量」中的 **Agent 持久化** 路径一致。

---

### A5. 教师使用 `POST .../chat`（Medium / 产品待定）

[getCourseIfMember](edu-platform/lib/course-access.ts) 对 **任课教师** 放行课程访问，故教师可对自有课程调用聊天接口。Phase 8 叙述以 **学生** 为主；若产品意图为「仅学生」，则需在路由中显式限制 `role === STUDENT`。

---

### A6. `QaLog` 落库字段不完整（Medium）

[maybePersistQaLog](edu-platform/lib/services/chatService.ts) 未写入 Prisma 模型中的 `metadata`、`agent_feedback`、`responseQuality`、`isHelpful` 等；Phase 8 决策 2 中 `metadata JSONB` 为扩展字段，当前 **恒为默认空/未用**。

---

## B. 安全问题（严重等级）

### Critical

- **B-C1**（已缓解）：原 **A1** 内部 `user_id` 与平台 UUID 语义错配；在 `course-rag-access` 增加映射解析后，**正常绑定下**课程腿授权应恢复正确。仍需防止映射表被篡改等运维层风险。

### High

- **B-H1**：EduAgent [AuthorizationChecker.require_http_key_if_configured](src/edu_agent/auth/checker.py) 在未配置 `EDU_AGENT_API_KEY` 时对 HTTP **不校验**密钥；若 Agent 进程暴露于非信任网络，任意客户端可尝试调用 HTTP API（仍受 `session_id` + `user_id` 与会话所有者校验约束，但缺少一层密钥纵深）。
- **B-H2**：在 **持有有效 Agent API 密钥** 且 **持有他人合法 `session_id`** 的前提下，请求可携带任意 `X-Platform-Course-Id`；Agent 每轮从 header 写入 `course_id`（[session_runner.py](src/edu_agent/runner/session_runner.py)），**会话与课程未在 Agent 侧强绑定**，存在跨课程 RAG 尝试面（实际能否命中还受平台内部校验正确性影响）。修复 A1 后仍建议将会话创建时的 `course_id` 与 header 对齐校验。

### Medium

- **B-M1**：[courseChatSseResponse](edu-platform/lib/services/chatService.ts) 在 Agent 非 2xx 时将响应体 `t.slice(0, 400)` 拼进 `ApiError` 消息返回客户端，可能 **泄露上游错误细节**。
- **B-M2**：[GET /api/v1/me/qa-logs/export](edu-platform/app/api/v1/me/qa-logs/export/route.ts) 使用 `findMany` **整行**返回，字段多于「导出所需最小集」，扩大响应中的敏感面（仍限本人数据）。
- **B-M3**：`qaCollectionEnabled` 在 **流开始**读取（[courseChatSseResponse](edu-platform/lib/services/chatService.ts)），流式过程中用户关闭采集与 `flush` 落库之间 **无事务/无二次读取**，存在 **TOCTOU**（竞态窗口通常可接受，但非严格「关采集即绝不落库」）。

### Low

- **B-L1**：[ChatComponent.tsx](edu-platform/components/ChatComponent.tsx) 数据采集 Modal **不阻塞**首次发送，用户可能在点击「已知悉」前发起请求（仍受服务端 `qa_collection_enabled` 约束，主要是 **体验与合规叙事** 风险）。
- **B-L2**：Next [middleware.ts](edu-platform/middleware.ts) **不包含** `/api`；所有 API 依赖各 Route 内 [getAuthFromRequest](edu-platform/lib/request-auth.ts)。若新增路由漏写鉴权，无全局兜底（**运维/代码审查** 类风险）。

### 已做得较好的点

- **JWT**：[verifyAccessToken](edu-platform/lib/jwt.ts) 校验 `issuer`、`HS256`，要求 `sub`、`username`、`role`；`agent_user_id` 可选写入 claims。
- **Agent 会话归属**：[Gateway.process_inbound_message](src/edu_agent/runner/gateway.py) 在 `get_session` 后调用 `require_session_user`，**user_id 与会话 owner 不一致则 forbidden**。
- **Prisma 查询**：分析侧 [analyticsService.ts](edu-platform/lib/services/analyticsService.ts) 使用 `$queryRaw` 模板参数绑定 `courseId`，**非字符串拼接**，降低 SQL 注入面。

---

## C. API 不一致问题

| 项 | 规格 / 文档 | 实现 | 说明 |
|----|-------------|------|------|
| 聊天入口 | 示例 `EventSource` + `GET .../chat-stream?session_id=` | `POST /api/v1/courses/{id}/chat`，body `{ message, lesson_id? }`，`fetch` + ReadableStream | 功能等价，**路径与方法不一致** |
| SSE 事件 | `type: text \| citation \| done`，`done` 含 `tokens`、`exec_time_ms` | [B3SseEvent](edu-platform/lib/services/chatService.ts) 与转换逻辑一致 | 符合 |
| `GET .../chat/history` 响应项 | 示例字段 `id, question, answer, created_at, hit_materials` | 额外返回 `session_id` | **向后兼容扩展**；若客户端严格校验可能报错 |
| `GET .../analytics` | `weak_concepts` 为结构数组 | [getCourseAnalytics](edu-platform/lib/services/analyticsService.ts) 恒返回 `weak_concepts: []` | **与示例/能力不一致** |
| `GET .../learning-progress` | 未规定是否按课程 | [getStudentLearningProgress](edu-platform/lib/services/analyticsService.ts) 按 **全局** `studentId` 聚合 `qa_logs` | 与「按课隔离聊天」并存时，**产品语义需文档化**（全课汇总 vs 按课） |
| 管理员历史 | 管理员 + `student_id` 可查 | 已按 **A2** 修复路径放行 | — |

---

## D. DB / Prisma 风险点

- **表结构**：[migration 20260511140000_phase8_qa_chat](edu-platform/prisma/migrations/20260511140000_phase8_qa_chat/migration.sql) 中 `qa_logs` 外键、`(course_id, created_at)`、`(student_id, created_at)`、`session_id`、`hit_materials` **GIN** 与 Phase 8 决策 2 的 SQL 片段 **一致**。
- **Prisma schema**：[QaLog](edu-platform/prisma/schema.prisma) 未声明 GIN（由 raw migration 维护）；后续若仅用 `prisma db push` 而 **丢失 migration** 可能 **缺索引** — 运维与迁移流程风险。
- **分区**：Phase 8「注意事项」建议按年/月分区；当前 **无分区**，大表扫描依赖时间过滤与索引 — **性能/归档** 类技术债。
- **软删**：`deleted_at` 与 `updateMany` 删除路径存在；分析查询普遍带 `deleted_at IS NULL` — **一致**。
- **LearningProgress 表**：未单独建模；进度为启发式 — 与文档「若需要」一致。

---

## E. 修复建议（具体可操作）

1. ~~**内部课程 RAG `user_id`（对应 A1 / B-C1）**~~ **（已做）**  
   - `course-rag-access`：`agentUserId` → `platformUserId` 解析 + Vitest。

2. ~~**管理员历史（对应 A2）**~~ **（已做）**  
   - `chat/history`：带 `student_id` 时管理员仅校验课程存在 + `requireAdmin`。

3. **Agent 纵深防御（对应 B-H2）**  
   - 在创建 `CourseChatSession` / Agent session 时写入并固定 `course_id`；Agent 每轮比对 header 与绑定 `course_id`，不一致则拒绝或忽略 header。

4. **错误与导出（对应 B-M1、B-M2）**  
   - Agent 失败消息仅返回固定错误码 + 服务端日志记录详情；导出接口 `select` 白名单字段。

5. **采集竞态（对应 B-M3）**  
   - 在 `maybePersistQaLog` 前使用 `prisma.$transaction` 内再次 `select qaCollectionEnabled` 或单条 `insert` 前条件更新（按产品选择）。

6. **文档与契约**  
   - 将 Phase 8 中 `X-Platform-User-Id` 说明改为「与 Agent `user_id` 一致（通常为 `agent_user_id`）」；聊天 URL 与 `EventSource` 示例与现实现对齐或标注「推荐 POST SSE」。  
   - `analytics.weak_concepts`：实现聚类/占位或明确标注「本阶段恒为空数组」。

7. **教师聊天（对应 A5）**  
   - 若产品禁止教师使用学生聊天：在 `POST .../chat` 上增加 `role === STUDENT`。

---

## 附录：与审计清单逐条对照（摘要）

| 清单主题 | 结论 |
|----------|------|
| 1 Agent 与课程边界 | `course_id`/`lesson_id` 在 runtime；`knowledge_query` 无 `course_id` 参数；`sources` 合法集合符合 Phase 7/8；命中 chunk/material/source 由 Agent [_accumulate_knowledge_hits](src/edu_agent/agent.py) 汇总。内部 `course-rag-access` 已解析 **agent → platform** id。 |
| 2 QA 日志与采集 | 关采集不写库仍回答；字段大部齐全；`metadata` 等未写；首次告知有 **不阻塞** 竞态；导出/删除存在。 |
| 3 多轮与 session | `session_id` 每课每生唯一映射；Agent 侧多轮上下文正确；无跨课会话共享。 |
| 4 SSE | `text`/`citation`/`done` 与统计字段符合；Agent 逐 chunk SSE；中断有 `STREAM_INCOMPLETE`/`done.error`。 |
| 5 API 契约 | 学生/教师/管理员路径见 **A2、C 表**；analytics `weak_concepts` 空数组。 |
| 6 Prisma/DB | 见 **D**；无分区。 |
| 7 安全 | 见 **B**；Prisma 使用得当处已注明。 |
| 8 并发与一致性 | 单条 `create`；会话队列见 Gateway；采集 TOCTOU 见 **B-M3**。 |

---

*本报告依据计划「Phase 8 B3 审计」生成。与报告同步落地的修复：`course-rag-access` 的 `agent_user_id` → `platform_user_id` 解析（及 Vitest）、`chat/history` 管理员审计路径。*
