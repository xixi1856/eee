# Phase 6（B1）系统级代码审计报告

**审计范围**：`edu-platform/`（与 `README.md` 同级）  
**对照规格**：`implement_docs/phase6.md`（含复核后已与实现对齐的绑定两阶段、Redis 等描述）  
**审计视角**：生产环境、攻击者视角、架构正确性（非 ESLint/风格）

> **复核说明（2026）**：首轮审计提出的问题已在代码与规格中落实。下文 **§0** 为修复摘要；**§A–E** 在保留原审计框架的基础上更新为**当前状态** + **仍建议关注的残余项**。首轮中已关闭的问题不再重复展开，必要时见 Git 历史或 `review_docs/review_phase6_reaudit.md`。

---

## 0. 首轮问题修复状态（摘要）

| 原编号 | 主题 | 状态 |
|--------|------|------|
| A2 / B Critical | `X-Forwarded-For` 伪造绕过 IP 限流 | **已修复**：`TRUST_PROXY_HOPS`（默认 0 忽略 XFF）、优先 `X-Real-IP`；`__tests__/request-auth.test.ts` 覆盖 |
| A3 / B Medium | 绑定失败无真实「封禁窗口」 | **已修复**：配置 `REDIS_URL` 时 Redis `ZSET` 计数 + 超限时 `SET bind:ban:{ip}` TTL；无 Redis 时回退 Prisma `credential_bind_attempts` |
| A4 / C | 教师与自助凭证、ADMIN UI 与 API 矛盾 | **已修复**：`GET /api/v1/credentials` 仅 `STUDENT`；`POST` 固定 403；教师/管理员 UI 与 README 一致；学生首码在**注册事务**内签发 |
| A6 | Refresh 并发双消费 | **已修复**：`refreshSession` 使用 `$transaction` + `updateMany` 且要求 `revoked.count === 1` |
| A1 | 绑定仅靠静态 API Key + code | **已缓解（架构增强）**：`POST /api/v1/bind/start` 校验 code 后写入 Redis **challenge→codeHash**；`POST /api/v1/bind/complete` 一次性消费 challenge 再落库；**仍需**保管好 `BIND_CREDENTIAL_API_KEY` 与 Redis |
| A5 | Prisma 多迁移基线 | **流程债**：README 已提示约定单一 `migrate deploy`；仓库是否仍含多条 init 需团队自行整理 |

---

## A. 架构级问题（当前视角）

### A1. 绑定信任边界（当前设计，非缺陷清单）

- **鉴权**：`bind/start` 与 `bind/complete` 均要求 `X-Platform-Bind-Key`（与 `BIND_CREDENTIAL_API_KEY` 常量时间比对）。
- **两阶段**：`start` 不消耗凭证，仅在校验 ACTIVE/未过期后签发短期 `bind_challenge_token`（Redis，`BIND_CHALLENGE_TTL_SEC`）；`complete` 用 `getDel` 消费 challenge 后在 DB 事务内 `updateMany(ACTIVE→USED)` 并 upsert 映射。
- **与旧版单 POST `bind-credential` 相比**：缩短「仅持 Key 即可在单请求内试错」的窗口；规格与 README 已改为描述该流程。
- **残余架构假设**：Agent 与 Redis、平台之间的网络与密钥仍属高敏感面；若需等价于「学生本人在场确认」，应在产品层由 Agent/客户端流程保证，而非仅靠平台 HTTP。

### A2. 客户端 IP 与限流（已修复后的残余注意点）

- 默认 **`TRUST_PROXY_HOPS=0`** 时不采用不可信 XFF；生产应在反向代理注入 **`X-Real-IP`**，或按需配置 `TRUST_PROXY_HOPS`（从 XFF 右侧数跳）。
- **残余**：未注入 `X-Real-IP` 且直连 Next 时，`getClientIp` 可能为 `"unknown"`，**所有客户端共享同一限流桶**（开发/错误部署时明显）。属运维与网关配置项，非逻辑 bug。

### A5. Refresh 事务内读取 Agent mapping（低优先级）

- `refreshSession` 事务内调用 `loadAgentUserIdForUser` 仍走全局 `prisma` 而非 `tx`；极端并发下几乎无影响，若追求洁癖可改为 `tx.agentIdentityMapping.findUnique`。

---

## B. 安全问题（严重等级，当前）

### High（仍为设计层建议，非未实现项）

- **`BIND_CREDENTIAL_API_KEY` 泄露**：仍可配合有效/未过期 `code` 完成两阶段绑定；须配合密钥轮换、网络隔离、Agent 侧秘密管理。
- **Access JWT 内 `role` 在 TTL 内不可撤销**：降级/封禁后旧 token 仍可能带旧角色至过期；高敏感操作可缩短 TTL 或引入会话版本号/黑名单（成本权衡）。

### Medium

- **绑定失败仅按 IP 维度**（规格曾提「用户或 IP」）：Agent 模型下「用户」难定义；若未来需减轻 NAT 共桶，可再加分桶策略。
- **Redis 可用但偶发命令失败**：`rateLimit` 对非 `ApiError` 会回退 Prisma；`recordBindFailure` 在 Redis 异常时写入 DB，长期可致 `credential_bind_attempts` 增长，建议运维定期清理或监控。

### Low

- **`metadata` JSONB** 未强约束 schema，长期建议约定版本字段。
- **过期凭证**：仍以懒标记（列表/绑定路径）为主，无全局定时任务时，纯离线报表可能短暂看到仍为 `ACTIVE` 的已过期行。

---

## C. API 与契约（相对 `phase6.md` 与 README，当前）

| 项目 | 说明 |
|------|------|
| `POST /api/v1/login` | 较 phase6 最简示例多 `refresh_token` + HttpOnly Cookie；README 已说明 |
| `POST /api/v1/register` | 201 返回 `{ user, credential? }`；**仅学生**含一次性 `credential`（注册事务内签发） |
| `GET /api/v1/credentials` | **仅 STUDENT**；`jsonOk({ credentials: list })`，无明文 `code` |
| `POST /api/v1/credentials` | **403**，自助生成已关闭（README 与 phase6 当前叙述一致） |
| `POST /api/v1/bind/start` | Body `{ code }`；Header `X-Platform-Bind-Key`；响应 `{ bind_challenge_token }`；**需 Redis** |
| `POST /api/v1/bind/complete` | Body `{ bind_challenge_token, agent_user_id, channel }`；响应含 `platform_user_id`、`channel_token` |
| 单路径 `POST /api/v1/bind-credential` | **已移除**，由上述两接口替代 |

---

## D. DB / Prisma（当前仍成立）

- 枚举、外键、`credentials.code_hash` 唯一、HMAC 存储、`agent_identity_mappings` 双 UNIQUE、相关索引：**与首轮结论一致，仍合理**。
- **风险点**：迁移历史治理（§A4）；`credential_bind_attempts` 在仅 Prisma 回退路径下的增长（§B Medium）。

---

## E. 建议修复 / 改进方向（剩余项）

1. **网关**：生产注入可信 `X-Real-IP` 或正确配置 `TRUST_PROXY_HOPS`；避免全站 `unknown` 共桶。
2. **运维**：Redis 密码、网络隔离；监控绑定 503/429；按需清理或归档 `credential_bind_attempts`。
3. **工程**：整理 Prisma 迁移基线；可选将 `loadAgentUserIdForUser` 纳入 refresh 的 `tx`。
4. **纵深防御（可选）**：Bind 来源 IP allowlist、mTLS、请求体大小限制。

---

## 逐项核对摘要（审计目标清单，更新后）

### 1. 用户与权限

| 检查项 | 结论 |
|--------|------|
| `users` + `STUDENT`/`TEACHER`/`ADMIN` | **是** |
| 需 JWT 的 API | **是**（`getAuthFromRequest` + `requireAuthenticated` / `requireAdmin`） |
| `bind/start`、`bind/complete` | **无用户 JWT**（Agent + API Key + 两阶段），符合当前规格 |
| `/api/v1/admin/**` | **是**（路由 + service 双重 ADMIN） |
| middleware 与 Handler | Handler 为权威鉴权；页面 matcher 覆盖 `/user`、`/credentials` 等 |

### 2. 凭证码系统

| 检查项 | 结论 |
|--------|------|
| 8 位、哈希存储、状态机、USED 不可复用、管理员脱敏与撤销 | **是**（与首轮一致） |
| 每小时生成上限 | **是**（`assertCredentialGenerationAllowed`）；注册首码在事务内**跳过**该上限（有意设计） |
| 绑定失败限流 + 封禁 | **是**（Redis 优先；Prisma 回退）；IP 来源见 **§A2** |

### 3. 绑定流程与并发

| 检查项 | 结论 |
|--------|------|
| 两阶段协议 | **是**（`start` / `complete`） |
| code ACTIVE、未过期、事务内 `updateMany` | **是** |
| 同 code 并发双绑 | **已缓解**（与首轮相同机制） |
| `agent_identity_mappings` 唯一性 | **是** |

### 4. JWT

| 检查项 | 结论 |
|--------|------|
| `sub`、`role`、`agent_user_id`（来自 DB 映射） | **是** |
| 验签、短期 access + refresh 轮换 | **是**；refresh **事务化单次消费** |

### 5. 密码与通用安全

| 检查项 | 结论 |
|--------|------|
| argon2id、无 raw SQL、lib 内未见敏感 console | **仍成立** |
| CORS / middleware | **仍成立**（首轮结论适用） |

---

## 结论（复核后）

B1 在 **身份、角色、凭证哈希、两阶段绑定、Redis challenge、IP 限流与封禁、Refresh 事务消费、学生注册首码与凭证 API 收敛** 等方面已与规格及 README 对齐，**可支撑进入 Phase 7（B2）** 的开发前提；上线前仍须在**网关 IP、Redis 与密钥运维、迁移基线**上落实 §E 与 §A2–A4 中的流程与环境项。

---

*本报告已按复核后代码修订。未包含渗透测试或负载测试；更细的复审记录见 `review_docs/review_phase6_reaudit.md`。*
