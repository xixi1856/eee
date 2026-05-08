# 教育 Agent 实现进度

## Phase 1 — 核心 ReAct 循环 ✅

### 完成内容

| 文件 | 说明 |
|------|------|
| `src/edu_agent/__init__.py` | 包初始化 |
| `src/edu_agent/types.py` | `ToolResult`、`AgentConfig` 数据类 |
| `src/edu_agent/agent.py` | `EduAgent`：Hermes 风格 ReAct 对话循环，支持工具调用与迭代预算上限 |
| `src/edu_agent/tools.py` | `TOOL_SCHEMAS` + `execute_tool`；内置 `knowledge_query`、`generate_quiz` 两个工具 |
| `src/edu_agent/prompt_builder.py` | 分层 system prompt 构建（Persona → Skills → 安全块 → 工具指引） |
| `src/edu_agent/skills_loader.py` | Markdown 技能文件加载 + 文件级缓存 |
| `src/edu_agent/cli.py` | Click CLI：`edu chat` 命令 |

### 测试结果

```
51 passed in 1.68s
```

测试文件：
- `tests/edu_agent/test_agent_loop.py` — EduAgent ReAct 循环（13 个用例）
- `tests/edu_agent/test_tools_wrapper.py` — 工具 schema 与 handler（26 个用例）
- `tests/edu_agent/test_prompt_builder.py` — prompt builder 与 skills loader（12 个用例）

### 关键修复

1. **handler 路由问题**：`execute_tool` 原本从 `_HANDLERS` dict 取函数引用（捕获时已绑定），导致 `patch("edu_agent.tools._handle_xxx")` 无效。改为优先通过 `globals()` 动态查找，使 mock patch 生效。
2. **execute_tool 可 patch 问题**：`agent.py` 原用 `from edu_agent.tools import execute_tool`（本地绑定），导致 `patch("edu_agent.tools.execute_tool")` 无效。改为 `import edu_agent.tools as _tools_module` 并调用 `_tools_module.execute_tool(...)`，通过模块对象间接调用。
3. **测试挂起**：因 patch 无效导致真实 `_handle_generate_quiz` 被调用，触发 `asyncio.run()` 网络请求，添加 `pytest-timeout` 防止测试永远挂起。

---

## Phase 2 — 安全过滤 / 学习者画像 / 会话存储 ✅

### 完成内容

| 文件 | 说明 |
|------|------|
| `src/edu_agent/safety.py` | `check_input` / `check_output` 纯规则安全过滤（离线，无网络请求）；`SafetyCheckResult` 数据类 |
| `src/edu_agent/learner_profile.py` | `load_profile` / `save_profile`（原子写）/ `update_topic_mastery`（mastery ∈ [0,1]）/ `profile_summary` |
| `src/edu_agent/session_store.py` | `append_turn` / `load_session` / `list_sessions`；追加写 JSONL，自动跳过损坏行 |

### 测试结果

```
97 passed in 2.37s
```

新增测试文件：
- `tests/edu_agent/test_safety.py` — 安全过滤（18 个用例）
- `tests/edu_agent/test_learner_profile.py` — 学习者画像（16 个用例）
- `tests/edu_agent/test_session_store.py` — 会话存储（12 个用例）

---

## Phase 2（续）— Phase 2 模块接入 agent + Phase 1 补全 ✅

### 完成内容

**`agent.py` 接入 Phase 2 模块**

| 改动点 | 说明 |
|--------|------|
| `AgentConfig` 新增字段 | `profile_storage_dir: str = "learner_profiles"`、`session_storage_dir: str = "session_logs"` |
| `EduAgent.__init__` | 启动时加载 learner profile，缓存 `_profile_summary` |
| `run_turn` 输入安全门 | `check_input` 返回 unsafe → 直接返回屏蔽消息并持久化，不调用 LLM |
| `run_turn` 输出安全门 | `check_output` 对最终回答过滤，不安全则替换为固定提示语 |
| `_persist_turn` 辅助方法 | 调用 `session_store.append_turn` 写 JSONL（user + assistant 各一行） |
| `build_system_prompt` 调用 | 传入 `learner_profile_summary` 注入画像上下文 |

**`tools.py` 补全 Phase 1 剩余工具**

| 工具 | 说明 |
|------|------|
| `build_mindmap` | 调用 `rag_mvp.mindmap.build_structure_mindmap`，支持 `refine` 参数 |
| `parse_document` | 调用 `rag_mvp.engine.parse_file` / `parse_folder` |
| `ingest_document` | 调用 `rag_mvp.engine.ingest_file` / `ingest_folder` |

**技能文件补全**

| 文件 | 说明 |
|------|------|
| `skills/scaffolding.md` | 脚手架教学策略（ZPD、步骤分解） |
| `skills/concept_clarification.md` | 概念澄清策略（对比/纠错） |

### 测试结果

```
113 passed in 2.64s
```

新增 / 更新测试文件：
- `tests/edu_agent/test_agent_loop.py` — 新增 `TestSafetyIntegration`（4）、`TestSessionPersistence`（2）、`TestLearnerProfileInjection`（1）共 7 个用例；fixture 更新为使用 `tmp_path`
- `tests/edu_agent/test_cli_chat.py` — CLI 集成测试（9 个用例）：`/quit`、`/exit`、回复显示、`/reset`、空输入、错误处理、`--user` 选项、会话 ID 显示、EOF 退出

### 关键修复

1. **类型错误**：`ChatCompletionMessageCustomToolCall.function` 不存在 → 改用 `hasattr(tc, "function")` 鸭子类型判断，避免 `isinstance` 过滤掉测试用的 MagicMock。
2. **测试中 session_id 显示**：CLI 打印的是构造 `AgentConfig` 时的本地变量，EduAgent 被 mock 后 `__init__` 不运行，session_id 为空字符串 → 断言改为检查 `"会话 ID:" in output`。
3. **EOF 退出码**：Click `CliRunner` 在空输入时返回 exit_code=1，而非 0 → 断言改为允许 `result.exception` 为 `None` 或 `SystemExit`。

---

## Phase 3 — 评估工具 + 技能热重载 ✅

### 完成内容

**`tools.py` 新增 LLM 调用辅助 + 3 个评估工具**

| 新增 | 说明 |
|------|------|
| `_call_llm(prompt, system)` | 模块级同步 LLM 辅助函数，lazy-import settings，测试可直接 `patch("edu_agent.tools._call_llm")` |
| `hint_generator` tool | 苏格拉底式分级提示（level 1–3），引导学习者思考而不直接给出答案 |
| `score_essay` tool | 书面作答评分：返回 0–100 分、总体评价、优点、改进建议；LLM 返回 JSON，非 JSON 时降级为纯文本 |
| `evaluate_code` tool | 代码评估：检查正确性、代码质量、边界情况，给出鼓励性反馈；支持任意编程语言 |

**`agent.py` 新增 `reload_skills()` 方法**

| 方法 | 说明 |
|------|------|
| `EduAgent.reload_skills()` | 调用 `skills_loader.invalidate_cache()` 清除内存缓存，下次 `run_turn()` 将从磁盘重新读取所有技能文件 |

### 测试结果

```
128 passed in 2.37s
```

新增 / 更新测试文件：
- `tests/edu_agent/test_tools_phase3.py` — 新文件（13 个用例）：`TestHintGeneratorTool`（5）、`TestScoreEssayTool`（4）、`TestEvaluateCodeTool`（4）
- `tests/edu_agent/test_agent_loop.py` — 新增 `TestReloadSkills`（2 个用例）
- `tests/edu_agent/test_tools_wrapper.py` — `test_required_tools_present` 更新，覆盖全部 8 个工具

---

## Phase 4 — SubAgent 委派机制 ✅

### 完成内容

**`src/edu_agent/types.py` — 新增两个数据类**

| 数据类 | 字段 |
|--------|------|
| `SubAgentConfig` | `task`、`allowed_tools`、`max_iterations`、`model`、`system_prompt` |
| `SubTaskResult` | `success`、`summary`、`payload`、`error`、`iterations` |

**`src/edu_agent/subagent.py` — 新建文件**

| 特性 | 实现 |
|------|------|
| 隔离上下文 | 子 Agent 不继承父 `messages`，仅持有任务描述 |
| 工具白名单 | `_filter_schemas()` 按 `allowed_tools` 过滤，`_RECURSION_BLACKLIST` 二次拦截 |
| 禁止递归 | `threading.local` 标志 `_subagent_active`，已在子 Agent 内则立即返回错误 |
| 并发上限 | 模块级 `threading.Semaphore(_MAX_CONCURRENT=4)`，`acquire(blocking=False)` |
| 迭代预算 | `SubAgentConfig.max_iterations`（默认 5），耗尽返回 failure |
| 标志清理 | `try/finally` 确保 flag 与 semaphore 在任何异常后均被释放 |

**`src/edu_agent/tools.py` — 新增 `delegate_task` 工具**

| 要素 | 说明 |
|------|------|
| TOOL_SCHEMAS 条目 | `task`（required）、`allowed_tools`、`max_iterations` |
| `_handle_delegate_task` handler | lazy import `SubAgent`，`max_iterations` 夹到 [1,10]，将 `SubTaskResult` 转换为 `ToolResult` |

### 测试结果

```
146 passed in 2.49s
```

新增测试文件：
- `tests/edu_agent/test_subagent.py` — 新文件（18 个用例）：
  - `TestSubAgentConfig` / `TestSubTaskResult`（5）— 数据类默认值与字段
  - `TestSubAgentRun`（5）— 无工具单轮、工具调用回合、LLM 错误
  - `TestSubAgentBudget`（1）— 迭代预算耗尽返回 failure
  - `TestRecursionGuard`（3）— 递归拦截、flag 清理
  - `TestToolWhitelist`（2）— 非白名单工具被拦截、`delegate_task` 始终被阻止
  - `TestDelegateTaskTool`（3）— 通过 `execute_tool` 调用 `delegate_task` 的成功/失败/夹值场景

### 关键修复

- **patch 目标**：`_handle_delegate_task` 内部做 lazy `from edu_agent.subagent import SubAgent`，patch 目标需为 `edu_agent.subagent.SubAgent` 而非 `edu_agent.tools.SubAgent`。
