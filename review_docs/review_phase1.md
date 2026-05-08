# A1 架构审计报告（复审版）

复审范围：针对你声明已整改的事项逐条复核代码与测试，不默认采信。第三轮复审已关闭 SubAgent 路径继承风险，四条新增关键测试可独立运行通过。

## 1. 符合设计的部分

- 主链路已与 `rag_mvp.config.settings` 解耦：当前仅 `src/edu_agent/tools_legacy.py` 仍引用旧配置，主运行路径未发现回退。
- `session_store` / `learner_profile` 已去除默认硬编码路径，`storage_dir` 改为关键字必填，杜绝无注入默认写盘。
- provider 元数据收敛到 registry：`credential_env_vars` 与 `default_api_key_when_unset` 已在 `ProviderSpec` 声明，loader 通过 `merge_env_credentials_into_provider_entry` 统一调用。
- `resolve_provider_runtime` 已增加 api_key fail-fast，且保留 `ollama` 默认 key 兜底策略。
- SubAgent 已在运行期设置并回收 `TurnRuntimeContext`，并在存在父 runtime 时生成 `:sub` 会话后缀。
- SubAgent `_run_with_runtime()` 在有父级上下文时优先继承 `parent.paths`，仅在无父级时（孤立运行）才回退 `build_paths(settings)`，杜绝 workspace/skills 覆盖漂移。
- 新增测试已覆盖上述核心修复点：
  - `test_resolve_provider_runtime_missing_api_key_raises`
  - `test_runtime_context_cleared_after_run_turn`
  - `test_subagent_sets_turn_runtime_with_parent_session_suffix`
  - `test_subagent_inherits_parent_paths_not_settings_defaults`

## 2. 偏离设计的部分

- 本轮复核未发现新增偏离项。上轮标注的路径继承风险已关闭（详见第 4 节）。

## 3. 隐藏技术债

- 兼容层语义（`LLM_*`/`.env` 并入 `EduSettings`）仍然存在，但定位清晰为入口层兼容，不属于 `rag_mvp.config` 回退。
- `runtime_context` 仍是单 `provider_runtime` 结构；多 purpose 显式建模（main/subagent/auxiliary 分离）仍可延期优化。
- 进程级缓存（`_WIKI_CACHE`、skills loader cache）仍是跨会话共享状态，潜在并发/测试顺序风险仍在。

## 4. 高风险实现

- ~~高风险（已关闭）：SubAgent 路径继承不一致风险。~~
  - 修复：`_run_with_runtime()` 优先取 `get_current_runtime().paths`，`RuntimeError`（无父级）时才回退 `build_paths(settings)`。
  - 回归测试：`test_subagent_inherits_parent_paths_not_settings_defaults` 构造带覆盖路径的父上下文，断言子上下文 `paths == parent_paths` 且 `skills_dir` 与默认路径不同，2 passed。
  - 当前无高风险开放项。

## 5. 不符合 Phase 约束的实现

- “不再从 `rag_mvp.config` fallback 读取配置”在主路径满足。
- `tools_legacy` 保留旧依赖属文档允许范围（非主路径），当前不判定为 A1 阻塞。

## 6. 建议立即修复的问题

- ~~立即修复 1（已关闭）：SubAgent 继承父 `paths`。~~ 已修复并有回归测试。
- ~~立即修复 2（已关闭）：补路径继承一致性回归测试。~~ 已补齐。
- 当前无待立即修复项。

## 7. 可以延期的问题

- 延期 1：进程级缓存隔离策略（per-session key / TTL / 测试清理钩子）。
- 延期 2：`TurnRuntimeContext` 的多 purpose 显式建模。
- 延期 3：retry 分类与 backoff 的专门单测。

# 测试质量审计报告（复审版）

## 1. 高质量测试

- 本轮新增测试对修复点具备真实性约束，且可独立运行通过。
- `tests/edu_agent` 全量运行通过，`TestSubAgentRuntimeContext` 2 passed。

## 2. 伪测试/脆弱测试

- `test_tools_phase3` 仍主要通过 patch `_call_llm` 验证字符串加工逻辑，对 runtime wiring 约束弱。
- `test_cli_chat` 仍以 patch `EduAgent` 为主，集成边界（真实 settings->agent wiring）验证深度有限。

## 3. 缺失的重要测试

- ~~缺失 1（已补齐）：SubAgent 路径继承一致性测试。~~
- 缺失 2：yaml/.env/env 三层优先级与冲突覆盖顺序测试。
- 缺失 3：invalid provider / invalid schema 负路径测试。
- 缺失 4：retry 分类/backoff 单测。

## 4. 过度 mock 问题

- `test_tools_phase3` 与 `test_cli_chat` 仍有过度 mock 倾向，建议后续增加少量薄集成测试补边界。

## 5. 不专业断言

- 部分宽松断言仍存在（例如“包含关键词即可”），但不影响本轮已修复项的真实性验证。

## 6. fixture/隔离问题

- `test_resolve_env_vars_substitution` 已改为 `monkeypatch.setenv`，此前环境污染风险已关闭。
- `with_turn_runtime` 的 token reset 仍正确。

## 7. 测试可信度评级

- 评级：A-（较上轮再提升）。
- 理由：关键架构修复点均有针对性回归测试且全量稳定通过；路径继承场景缺口已补齐；剩余缺口（三层优先级、负路径、retry）属延期合理范围。

# 最终结论

- 架构完成度评分：9.2 / 10
- 测试可信度评分：8.7 / 10
- 是否达到"可进入 phase2"的标准：达到，无阻塞项。

结论说明：

- 所有"立即修复"项已实质关闭并通过回归测试验证。
- 当前无中高风险开放项；剩余技术债（缓存隔离、多 purpose runtime 建模、三层配置优先级测试）均可延期，不阻塞 phase2。
- 建议在 phase2 中跟进：配置层三层优先级覆盖测试、provider 负路径测试。