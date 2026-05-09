# A2 架构审计报告（复审版，2026-05-08 回归合并，Hermes 对齐基线）

## 0. 审计范围与基线

本次审计基于三条证据链：

1. 当前仓库实现（sessions/context/agent/cli/runtime/tools/tests）
2. Hermes Agent 公开设计与文档（联网核验）：官方文档 [Context Compression and Caching](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching)，以及公开仓库中的 `context_compressor.py`、`context_engine.py`、`gateway/run.py` 分层语义
3. 自动化验证：`pytest tests/edu_agent/test_context_manager.py tests/edu_agent/test_session_store.py tests/edu_agent/test_agent_loop.py` — **49 passed**（约 37s，2026-05-08）

Hermes 关键机制对照（文档与实现语义）:

- 分层预算：threshold + target_ratio + tail token budget
- 压缩前工具结果裁剪（tool prune）
- 压缩后工具链配对修复（sanitize tool pairs）
- 摘要失败可见降级 + cooldown
- 摘要连续更新（previous summary iterative update）
- 强摘要边界语义（REFERENCE ONLY + END OF CONTEXT SUMMARY，与 Hermes 示例一致）

---

## 1. 符合设计部分

### 1.1 Session 持久化结构化落地（已达标）

- 已完成 Session / Message / ToolCall 三层拆分，非 JSON blob 假结构化。
- SQLite schema、索引、级联删除完整，支持按用户、状态、关键字、工具调用查询。
- 关键文件：
  - src/edu_agent/sessions/schema.py
  - src/edu_agent/sessions/store.py
  - src/edu_agent/sessions/models.py

### 1.2 Context 管理与 Hermes 主要思想已对齐（已达标）

- 有 token **粗估 + 精估**双层；决策侧 `max(rough, last_api_prompt)`（`TokenBudgetEngine.display_tokens`）。
- 有 **网关式预压**：`gateway_hygiene_ratio`（默认 0.85）+ `run_turn` 内在调用 LLM 前基于粗估的 `force` 压缩。
- 有 tail-token-budget + head/tail 保护参数（`protect_first_n` / `protect_last_rounds` / `compression_target_ratio` / `protect_last_n_messages`）。
- 有 tool 输出裁剪（middle 区域）和 tool pair sanitize；可选 **`max_tool_chains_pulled_into_tail`** 缓解高工具密度下 tail 被无限左拉。
- 有 summary 失败降级、cooldown，以及 **`format_compaction_summary_body`**：`[CONTEXT COMPACTION]` + **REFERENCE ONLY** + **END OF CONTEXT SUMMARY** 边界文案。
- 有 **`record_compaction_failure`** 对同类失败消息的 **update 去重**（避免连续失败堆叠多条 system 标记）。

### 1.3 Agent 集成路径正确（已达标）

- run_turn 内已有 persistence 写入路径（user / assistant / tool）
- 每轮后压缩与 reload 回流内存
- archived 会话写保护有效；**写路径内** `_require_session_writable` 与 `_write_lock` 同事务，避免 ARCHIVED 并发写入
- CLI 已支持 --session-id 恢复 / list-sessions / cleanup-sessions

---

## 2. Hermes 机制摘要（联网结论）

| 机制 | Hermes 行为 |
|------|-------------|
| **架构** | `ContextEngine` ABC；默认 `ContextCompressor`，可插件替换（如 LCM）。 |
| **双层压缩** | **Gateway**：约 **85%** 上下文、粗估 token、**历史 ≥4 条** 才触发。**Agent**：约 **50%**（可配）、优先 **API 上报 prompt tokens**。 |
| **算法** | Phase1 长 tool 占位替换 → Phase2 头/中/尾 + 尾 token 预算 + 边界对齐不拆 tool 链 → Phase3 辅助模型摘要（可迭代旧摘要）→ Phase4 组装 + `_sanitize_tool_pairs`。 |
| **配置** | `threshold`、`target_ratio`、`protect_last_n`；文档中 `protect_first_n` 为首段硬保护。 |
| **其它** | Anthropic **prompt caching**；摘要模型窗口不足时文档描述可能 **无摘要丢弃 middle** 的降级路径。 |

---

## 3. 问题闭环与残余风险

### 3.1 历史审计项状态（2026-05-08 回归）

| 原项 | 原严重度 | 当前状态 |
|------|-----------|----------|
| Session 状态 TOCTOU（检查在锁外、写入在锁内） | P0 | **已修复**：`append_message` / `update_message` / `replace_session_messages` 均在 `_execute_write` 内调用 `_require_session_writable`。 |
| 摘要前缀弱于 Hermes | P1 | **已修复**：`format_compaction_summary_body` 含 REFERENCE ONLY 与 END OF CONTEXT SUMMARY。 |
| 高工具密度导致 middle 趋零、压缩失效 | P1 | **已缓解**：`max_tool_chains_pulled_into_tail` + `test_compress_max_tool_chains_cap_leaves_middle`。 |
| compaction failure marker 累积 | P2 | **已修复**：`record_compaction_failure` 命中已有片段则 **update**。 |
| `update_session_status` 对不存在 session 无信号 | P2 | **已修复**：抛出 `SessionNotFoundError`。 |
| `replace_session_messages` 异常无 rollback | 数据一致性 | **已修复**：`except` 内 `rollback`。 |

### 3.2 残余问题（非阻断）

**P2（低）**

1. **Gateway 与 Hermes 的 `len(history) >= 4` 条件**：本实现主要依据 `rough >= cap`；若需严格对齐 Hermes 网关语义，可在 `agent.py` 增加消息条数下限（或可配置）。
2. **默认 `token_limit_percent=0.6`**：Hermes 文档常用主压缩约 **50%**；建议在配置或 README **显式说明** 与 Hermes 默认差异。
3. **网关强制压缩失败**：当前仅 `logger.exception`；若需与 `_maybe_compress` 对称，可评估是否在失败时也 `record_compaction_failure`（权衡噪声）。

**P3（建议）**

4. **半集成测试**：更长 token、更接近生产的 summarizer 路径，验证多轮收敛性。

---

## 4. 数据一致性风险

1. ~~并发状态竞争（原 P0）~~：写路径已锁内校验 ARCHIVED，**主要风险已关闭**；仍建议可选补充「归档与 append 并发」测试以封顶回归。
2. `replace_session_messages` 已有显式 **rollback**；单连接事务语义清晰。
3. 读取侧无锁（允许），依赖 SQLite WAL 一致性语义；可接受，建议在运维文档中说明「读已提交快照」预期。

---

## 5. 生命周期与 replay 风险

### 已改善

- `_is_summary` 已可往返（DB is_summary ↔ runtime `_is_summary`）
- `sanitize_tool_pairs` 可补齐缺失 tool result stub，避免 tool_call_id 断链
- 双次压缩后 summary 保留已有测试覆盖
- 高工具密度可通过 **`max_tool_chains_pulled_into_tail`** 配置降级

### 仍需关注（策略层）

- 未设置链数上限时，极端工具密度会话仍可能更频繁依赖 trim/overflow 路径；属策略取舍而非单一实现缺陷。

---

## 6. Agent runtime 与 persistence 一致性

### 一致性现状

- run_turn 中 user/assistant/tool 均经 ContextManager → SessionStore 持久化。
- `_maybe_compress` 后会 reload DB 到内存，避免 runtime/DB 偏差积累。
- archived 入口保护在 agent 和 store 双层存在；**store 层 TOCTOU 已消除**，双层语义一致。

### 一致性说明

- 可选增强：网关压缩失败路径的持久化提示（见 §3.2）。

---

## 7. 测试质量审计报告

### 7.1 高质量测试

- tests/edu_agent/test_context_manager.py  
  - test_compress_preserves_early_tool_chain  
  - test_trim_preserves_summary_message  
  - test_sanitize_tool_pairs_inserts_stub  
  - test_double_compression_keeps_summary_in_db  
  - test_compress_static_fallback_when_summarizer_none（REFERENCE ONLY / END MARKER）  
  - test_compress_max_tool_chains_cap_leaves_middle  
  - test_record_compaction_failure_dedupes  
- tests/edu_agent/test_session_store.py  
  - 并发 append、归档只读、replace seq 重置  
- tests/edu_agent/test_agent_loop.py  
  - TestSessionResume、TestSessionPersistence  

### 7.2 仍建议补充

1. 并发：**ARCHIVED** 与 **append** 竞态（设计已修，用例可封顶）
2. **gateway_hygiene** 强制压缩失败路径的业务断言（若产品需要可见降级）
3. **replace_session_messages** 异常后 DB 状态断言（rollback 行为）

### 7.3 过度 mock 情况

- Agent 层大量 mock LLM 属可接受单测策略；半集成测试仍为 P3 建议项。

### 7.4 测试可信度评级

- 复审文档记载上次：中等偏低；复审合并前：约 72/100  
- **本次（合并后）**：约 **78/100**（P0/P1/P2 修复与用例补强）

---

## 8. 对 Hermes 对齐度结论

### 已对齐（实现或等价）

- 分层预算、`max(rough, api_prompt)` 思想  
- Gateway 高阈值预压 + Agent 主压缩  
- tail budget + head/tail 保护  
- tool prune + tool pair sanitize + 边界对齐  
- summary failure cooldown  
- previous summary 迭代更新  
- **强摘要边界语义**（REFERENCE ONLY + END MARKER）  

### 差异或未完全对齐（非阻断，可文档化）

| 项 | 说明 |
|----|------|
| Gateway 消息条数 | Hermes **≥4 条**；本实现未强制。 |
| 可插拔 Engine | Hermes YAML + 注册表；本仓库固定 `TokenBudgetEngine` + `compress_messages`。 |
| 结构化摘要模板 | Hermes 文档模板；本仓库依赖 summarizer，代码未强制同一字符串模板。 |
| Prompt caching | Hermes 有 Anthropic 集成；本仓库未实现。 |
| 无摘要时 middle | Hermes 部分路径可能静默丢；本仓库静态 fallback，更可见、略占 token。 |
| 细粒度可观测字段 | Hermes 生态中的 dropped_count 等；本仓库未系统性对外暴露。 |

---

## 9. 最终结论

| 指标 | 结论 |
|------|------|
| **A2 架构完成度（主观）** | **约 92%**（较早期复审 ~88% 提升，主因 P0 与多项 P1/P2 关闭） |
| **数据可靠性** | **绿偏黄** — ARCHIVED 与 replace 语义已加强；可选并发测试封顶 |
| **replay 一致性** | **良好** |
| **测试可信度** | **约 78/100** |
| **Hermes 上下文/压缩思想对齐度** | **高** |
| **是否可进入 A3** | **可以**；后续小步项见 §10 |

---

## 10. 建议后续项（按优先级）

1. 可选：网关分支增加 **`len(messages) >= 4`**（或与 Hermes 一致的可配置常数）。  
2. 可选：网关 `force` 压缩失败时是否 **持久化** 用户可见提示（与 `_maybe_compress` 对称）。  
3. 测试：并发 ARCHIVED + append；gateway 失败路径；半集成压缩收敛。  

---

## 11. 执行记录

**2026-05-08 回归合并前复审切片（历史）**

- tests/edu_agent/test_context_manager.py  
- tests/edu_agent/test_session_store.py  
- tests/edu_agent/test_agent_loop.py::TestSessionResume  
- tests/edu_agent/test_agent_loop.py::TestSessionPersistence  
- tests/edu_agent/test_cli_chat.py  
- 结果：全部通过  

**2026-05-08 合并时全量切片**

- `uv run pytest tests/edu_agent/test_context_manager.py tests/edu_agent/test_session_store.py tests/edu_agent/test_agent_loop.py -q`  
- 结果：**49 passed**  

**外部参考**

- [Hermes Agent — Context Compression and Caching](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching)
