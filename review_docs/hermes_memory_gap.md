# Hermes-agent 记忆机制 vs EduAgent A3 — 对标与差距

本文档对照 **NousResearch/hermes-agent** 公开源码与文档（以 `main` 分支为准，阅读日期以 Git 为准），与 **EduAgent** 当前 `src/edu_agent/memory/` 实现做能力矩阵与改造优先级说明。

## 权威来源（GitHub）

| 资源 | URL |
|------|-----|
| MemoryProvider 抽象 | https://github.com/NousResearch/hermes-agent/blob/main/agent/memory_provider.py |
| MemoryManager 编排 | https://github.com/NousResearch/hermes-agent/blob/main/agent/memory_manager.py |
| 用户功能说明 memory | https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md |
| Memory providers 配置 | https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md |

## Hermes：MemoryProvider 契约（源码摘要）

`MemoryProvider`（`memory_provider.py`）核心与可选钩子：

| 方法 | 说明 |
|------|------|
| `name` (property) | 短标识，如 `builtin` / `honcho` |
| `is_available() -> bool` | 无网络；仅检查配置与依赖 |
| `initialize(session_id, **kwargs) -> None` | 会话级初始化；`kwargs` 含 `hermes_home`、`platform`、`agent_context`、`agent_identity` 等 |
| `system_prompt_block() -> str` | 默认 `""`；静态系统提示片段 |
| `prefetch(query, *, session_id="") -> str` | 每轮前召回；默认 `""` |
| `queue_prefetch(query, *, session_id="") -> None` | 为下一轮排队预取 |
| `sync_turn(user_content, assistant_content, *, session_id="") -> None` | 每轮后持久化 |
| `get_tool_schemas() -> list[dict]` | OpenAI function 格式 schema |
| `handle_tool_call(tool_name, args, **kwargs) -> str` | 返回 JSON 字符串 |
| `shutdown() -> None` | 清理 |
| 可选 | `on_turn_start`、`on_session_end`、`on_session_switch`、`on_pre_compress`、`on_memory_write`、`on_delegation` 等 |

## Hermes：MemoryManager 编排（源码摘要）

`MemoryManager`（`memory_manager.py`）：

- `add_provider(provider)`：**至多一个非 builtin 外部 provider**；第二个被拒绝并打日志。
- `build_system_prompt()`：合并各 provider 的 `system_prompt_block()`。
- `prefetch_all(query, *, session_id="")`：合并 `prefetch()`。
- `queue_prefetch_all` / `sync_all`：逐 provider 调用。
- `get_all_tool_schemas()` / `handle_tool_call`：工具名路由到 provider；**去重** tool name。
- 生命周期：`initialize_all`、`on_session_end`、`on_session_switch`、`shutdown_all` 等。
- **输出清理**：`sanitize_context(text)`、`StreamingContextScrubber`（处理流式分片中的 memory fence）、`build_memory_context_block(raw)` 包装预取上下文。

## EduAgent A3 现状（仓库）

| 组件 | 路径 | 作用 |
|------|------|------|
| 存储 | `memory/storage.py` | Facts JSONL、concepts/profile JSON |
| 提取 / 协整 / 检索 | `extractor.py` / `consolidator.py` / `retriever.py` | LLM 抽事实、聚合、TF-IDF 检索 |
| 工具 | `tools/memory.py` | `remember_fact` / `search_memory` / `update_profile_note`（全局 registry） |
| 编排（本 PR 起） | `memory/coordinator.py` | 检索块拼装、阈值 consolidator 策略入口 |
| 管理器（本 PR 起） | `memory/manager.py` | Hermes 式 `add_provider` / `prefetch_all` / `build_system_prompt` 聚合 |
| Provider（本 PR 起） | `memory/provider.py` | `EduMemoryProvider` + `BuiltinFilesystemMemoryProvider` + 外部槽占位 |
| 输出清理（本 PR 起） | `memory/output_scrubber.py` | 非流式全文轻量清理；流式占位接口 |

## 能力矩阵

| Hermes 能力 | EduAgent | 差距 / 优先级 |
|-------------|----------|----------------|
| MemoryManager 单入口 | `EduMemoryManager` + `MemoryCoordinator` | **P1 已落地骨架**；后续可把 CLI `finalize` 也挂到 `on_session_end` 风格 API |
| 单外部 provider 槽 | `NullExternalMemoryProvider` + `add_provider` 拒绝第二个 external | **P2 占位**；未接 Mem0/Honcho |
| prefetch 合并 | `prefetch_all` → builtin `prefetch` 使用 Retriever | **P1** |
| sync_turn 每轮 | 仍以阈值 + 会话结束 consolidator 为主；未逐轮 LLM sync | **P2** 可选对齐 |
| 工具 schema 仅来自 provider | 记忆工具仍在 `tools/memory.py` 全局注册；builtin `get_tool_schemas()` 返回 `[]` 避免重复 | **P2** 若迁移工具进 provider 需改 registry |
| StreamingContextScrubber | `sanitize_completed_output` + 流式 `identity` 包装预留 | **P1 轻量**；完整状态机对齐 Hermes 为后续 |
| 语义 / 向量记忆（PR #727 等） | A3 不做向量 | **不对标**（A4+） |

## 改造优先级（执行顺序）

1. **P0**：本文档 +（可选）`docs/phase3.md` 附录链接本文。
2. **P1**：`MemoryCoordinator`、`EduMemoryManager`、`EduMemoryProvider` 实现；`EduAgent` 使用 coordinator 构建注入块与阈值 consolidator；输出 scrub 非流式。
3. **P2**：将记忆工具迁入 provider 并单一注册源；外部 provider 真实实现；流式 `StreamingMemoryScrubber` 对齐 Hermes。

## 参考：Hermes `MemoryManager` 公开方法名（便于二次核对）

`add_provider`, `build_system_prompt`, `prefetch_all`, `queue_prefetch_all`, `sync_all`, `get_all_tool_schemas`, `get_all_tool_names`, `has_tool`, `handle_tool_call`, `on_turn_start`, `on_session_end`, `on_session_switch`, `on_pre_compress`, `on_memory_write`, `on_delegation`, `shutdown_all`, `initialize_all`, `sanitize_context`, `build_memory_context_block`, `StreamingContextScrubber`.

（以仓库 `agent/memory_manager.py` 为准，若上游重命名请以 Git 为准。）
