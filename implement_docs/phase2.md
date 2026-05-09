# Phase 2：A2 上下文管理与会话存储详细方案

## 目标与背景

A1 完成后，EduAgent 已经拥有独立的配置体系与 provider 运行时。A2 的核心目标是建立面向生产的**会话存储与上下文管理**系统，以支持：

1. **会话生命周期管理**：创建、恢复、归档、搜索。
2. **Token 预算与上下文裁剪**：当单个会话内消息累积超过 LLM token 上限时，自动裁剪历史而不破坏核心信息。
3. **消息持久化**：用 SQLite 替代当前 append-only JSONL，支持结构化查询与工具调用记录。
4. **简单会话检索**：按关键词、时间范围、工具调用类型查询历史。

A2 完成后，EduAgent 应该具备以下特征：

- 任何 session 在任何时间重启后都可精确恢复（消息顺序、工具调用状态、当前上下文）。
- 单会话超长对话时自动进行智能压缩，而非简单截断。
- 会话、消息、工具调用三层数据模型清晰分离，可独立查询。
- 支持从 CLI 、API、第三方 channel 等不同入口加载同一 session，内部状态一致。

## 架构决策

### 决策 1：SQLite 作为本地会话数据库

当前使用 append-only JSONL，存在以下问题：

- 无法更新已写入的消息（例如工具调用的最终结果补写、会话元数据变更）。
- 无结构化索引，全量文件扫描才能搜索。
- 多进程并发写时容易出现 race condition。

采用 **SQLite** 原因：

- 单文件部署，无外部依赖。
- 支持 ACID 事务，并发写安全。
- 支持索引与 SQL 查询。
- 文件大小适中（单会话通常 < 10 MB）。
- Python 标准库原生支持。

**否决 PostgreSQL 作为本地存储**：当前项目阶段，Agent 还是本地工具，无必要引入服务级 DB。

### 决策 2：会话存储模型为三层结构

```
Session
  ├─ metadata（创建时间、状态、user_id 等；**不含**业务 channel 专有字段）
  └─ messages[]*
       ├─ metadata (role、timestamp、usage token...)
       └─ content
            ├─ text
            └─ tool_calls[]*
                 ├─ function_name
                 ├─ arguments
                 └─ result
```

**不采用简单的"消息列表"模型**的原因：

- 工具调用跨多轮交互：user → assistant(tool_call) → tool result → assistant(response)。简单线性模型无法表达"哪个 tool_call 的结果"。
- Token 计数需要细粒度，压缩时需要逐条选择哪些消息/工具结果保留。

### 决策 3：Token 预算与压缩策略

定义 `context_token_limit`（默认 60% × model_max_tokens）。

当 `sum(all_messages_tokens) > context_token_limit` 时触发压缩，按以下优先级裁剪：

1. **保留**：最后 3 轮 user + assistant 对话（确保当前意图明确）。
2. **保留**：所有涉及 tool_call 的消息（工具交互历史重要）。
3. **压缩**：中间旧消息进行摘要，替换为 system message 形式的"之前讨论过..."。
4. **如仍超出**：逐条删除最早的非工具消息。

**不采用"滑动窗口"的原因**：

- 简单截断会丢失上下文，导致 Agent 健忘。
- 摘要虽然有信息损失，但保留关键信息更符合教育场景。

### 决策 4：会话状态机

```
ACTIVE ──► IDLE (> max_idle_time)
  ├─ PAUSED (用户主动暂停)
  └─ ARCHIVED (用户归档)
```

- **ACTIVE**：当前会话可读写。
- **IDLE**：自动转换，可恢复为 ACTIVE。
- **PAUSED**：用户主动暂停，不会自动恢复。
- **ARCHIVED**：会话归档，只读不写。

### 决策 5：会话查询接口与索引

支持的查询维度：

- `search_by_session_id(session_id) -> Session`
- `search_by_keyword(keyword, date_range, status) -> list[Session]`
- `search_by_user(user_id, limit) -> list[Session]` （最近 N 个）
- `search_by_tool_call(tool_name, date_range) -> list[Session]`

教育平台、LMS、网关等均为 **channel**：只映射到统一的 `user_id` / `session_id` / 消息体，**不在** EduAgent 核心类型或会话元数据里承载课程、班级等业务字段。

建立以下索引避免全表扫描：

- `(session_id)` PRIMARY KEY
- `(user_id, created_at DESC)`
- `(status, updated_at DESC)`

补充：为后续 A5/B3 的历史检索体验，建议在 SQLite 侧预留 FTS5 虚拟表（message_content），用于关键词检索与标题生成。A2 可以先完成基础索引，FTS5 作为同阶段可选增强，不阻塞主路径。

### 决策 6：不在 A2 做多用户权限隔离

A2 的会话只负责单用户场景（CLI 或单 channel 单用户）。多用户隔离（授权、租户级别的 RAG namespace）留到 A4/A5 与 B1。

### 决策 7：消息编码格式统一为 OpenAI schema

```python
Message = {
    "role": "user" | "assistant" | "system",
    "content": str | list[content_block],  # 支持 text + tool_calls
    "tool_calls": [ToolCall],  # 仅 assistant message 有
    "tool_call_id": str,  # 仅 tool result 有
}

ToolCall = {
    "id": str,
    "type": "function",
    "function": {
        "name": str,
        "arguments": str,  # JSON string
    }
}

ToolResult = {
    "role": "user",
    "tool_call_id": str,
    "content": str,  # 工具返回的结果
}
```

**不采用自定义消息格式**：OpenAI schema 已成事实标准，减少适配代码。

## 文件清单

### 新建文件

- [src/edu_agent/sessions/__init__.py](e:/appProjects/eee/src/edu_agent/sessions/__init__.py)
  职责：sessions 子包导出入口。

- [src/edu_agent/sessions/models.py](e:/appProjects/eee/src/edu_agent/sessions/models.py)
  职责：定义 `Session`、`Message`、`ToolCall`、`ToolResult`、`SessionMetadata`、`MessageMetadata` 等数据模型（Pydantic）。

- [src/edu_agent/sessions/schema.py](e:/appProjects/eee/src/edu_agent/sessions/schema.py)
  职责：SQLite 建表 SQL、索引定义、schema 初始化逻辑。

- [src/edu_agent/sessions/store.py](e:/appProjects/eee/src/edu_agent/sessions/store.py)
  职责：`SessionStore` 类，提供：
  - `create_session(user_id) -> Session`
  - `get_session(session_id) -> Session`
  - `append_message(session_id, message) -> None`
  - `update_message(session_id, message_id, updates) -> None`
  - `list_messages(session_id, limit, offset) -> list[Message]`
  - `update_session_status(session_id, status) -> None`
  - `search_sessions(user_id, keyword, status, date_range) -> list[Session]`
  - `archive_session(session_id) -> None`

- [src/edu_agent/context/models.py](e:/appProjects/eee/src/edu_agent/context/models.py)
  职责：定义 `ContextConfig`（token_limit、compression_ratio、idle_timeout_sec）、`CompressionStrategy`。

- [src/edu_agent/context/calculator.py](e:/appProjects/eee/src/edu_agent/context/calculator.py)
  职责：提供：
  - `estimate_tokens(message, model_name) -> int`（基于 tiktoken 或模型-specific 方案）
  - `estimate_messages_tokens(messages, model_name) -> int`
  - `get_context_limit(model_name, config) -> int`

- [src/edu_agent/context/compressor.py](e:/appProjects/eee/src/edu_agent/context/compressor.py)
  职责：提供：
  - `compress_messages(messages, token_limit, current_model) -> list[Message]`
  - 实现优先级裁剪算法（保留最后 3 轮、保留工具调用）
  - 调用 LLM 生成摘要信息

- [src/edu_agent/context/manager.py](e:/appProjects/eee/src/edu_agent/context/manager.py)
  职责：`ContextManager` 类，聚合 SessionStore、token 计算、压缩；提供：
  - `load_context(session_id) -> list[Message]`（返回已压缩的消息列表）
  - `add_message(session_id, message) -> None`
  - `check_and_compress(session_id) -> None`（主动检查并压缩）

- [tests/edu_agent/test_session_store.py](e:/appProjects/eee/tests/edu_agent/test_session_store.py)
  职责：验证 CRUD、并发写、索引查询、状态转换。

- [tests/edu_agent/test_context_manager.py](e:/appProjects/eee/tests/edu_agent/test_context_manager.py)
  职责：验证 token 计算、压缩算法、摘要生成、边界情况。

### 修改文件

- [src/edu_agent/types.py](e:/appProjects/eee/src/edu_agent/types.py)
  变更：`AgentConfig` **不**增加任何 channel 专有字段（如课程、班级、租户）。教育类产品入口与 CLI 一样，只使用通用身份与会话标识；业务上下文若需要，放在 channel 适配层或消息正文/RAG，不进入核心配置模型。

- [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py)
  变更：
  - 构造函数新增 `session_store: SessionStore | None` 注入
  - `run_turn()` 改为先从 session store 加载历史，再构建 messages for LLM
  - 每轮回复后，将 user/assistant/tool_result 消息追加到 session store
  - 集成 `ContextManager` 的压缩逻辑，每轮结束前检查是否需要压缩
  - 支持从既有 session_id 恢复会话

- [src/edu_agent/learner_profile.py](e:/appProjects/eee/src/edu_agent/learner_profile.py)
  变更：
  - 无变更或轻微变更（profile 仍由 A3 专门维护）
  - 注：如果当前 learner_profile 在 agent.py 中被初始化，确保也通过 paths 注入。

- [src/edu_agent/cli.py](e:/appProjects/eee/src/edu_agent/cli.py)
  变更：
  - 新增 `--session-id` 参数，允许用户恢复某个会话
  - 新增 `list-sessions` 子命令，展示最近会话列表
  - 每次启动时检查 workspace 中是否存在 sessions.db，若无则初始化
  - 退出时主动调用 `session_store.close()`

- [README.md](e:/appProjects/eee/README.md)
  变更：补充会话恢复、会话列表查询的使用说明。

## 接口契约

### 1. SessionStore 核心接口

```python
class SessionStore:
    def __init__(self, db_path: Path) -> None: ...
    
    def create_session(self, user_id: str) -> Session: ...
    
    def get_session(self, session_id: str) -> Session | None: ...
    
    def append_message(self, session_id: str, message: Message) -> Message: ...
    
    def update_message(self, session_id: str, message_id: str, updates: dict) -> None: ...
    
    def list_messages(
        self,
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Message]: ...
    
    def update_session_status(self, session_id: str, status: SessionStatus) -> None: ...
    
    def search_sessions(
        self,
        user_id: str,
        keyword: str | None = None,
        status: SessionStatus | None = None,
        date_range: tuple[datetime, datetime] | None = None,
        limit: int = 20,
    ) -> list[Session]: ...
    
    def archive_session(self, session_id: str) -> None: ...
    
    def close(self) -> None: ...
```

### 2. ContextManager 核心接口

```python
class ContextManager:
    def __init__(
        self,
        store: SessionStore,
        config: ContextConfig,
        settings: EduSettings,
    ) -> None: ...
    
    def load_context(self, session_id: str) -> list[Message]: ...
    
    def add_message(self, session_id: str, message: Message) -> None: ...
    
    def check_and_compress(self, session_id: str) -> None: ...
```

### 3. 数据模型

```python
class SessionMetadata(BaseModel):
    id: str
    user_id: str
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    title: str | None

class Session(BaseModel):
    metadata: SessionMetadata
    messages: list[Message]

class MessageMetadata(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    timestamp: datetime
    token_count: int

class Message(BaseModel):
    metadata: MessageMetadata
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

class ToolCall(BaseModel):
    id: str
    function_name: str
    arguments: str  # JSON string

class ContextConfig(BaseModel):
    token_limit_percent: float = 0.6  # 相对 model_max_tokens
    compression_ratio: float = 0.5  # 摘要后消息数不超过原来的 50%
    idle_timeout_sec: int = 3600
```

## 实施顺序

1. 定义 models、schema，创建 SessionStore 基础。
2. 实现 SessionStore 的 CRUD 与查询。
3. 实现 token 计算与压缩算法。
4. 集成到 Agent.run_turn()，确保消息读写正确。
5. 改造 CLI，支持会话恢复与列表查询。
6. 补充测试。

## 注意事项

### 1. SQLite 并发写

若同一进程下 Agent 运行多个并发 session（A5+ 才可能），需要使用 connection pool 或 queue 来序列化写操作。A2 暂不支持同进程多并发会话，留到 A5。

#### 1b. ARCHIVED 与写锁（避免 TOCTOU）

- `SessionStore` 对 `append_message`、`update_message`、`replace_session_messages` 在 **同一把 `_write_lock` 内** 再次读取 `sessions.status`，确保「检查非 ARCHIVED」与「写入 messages」之间不会被其他线程插入 `archive_session`。
- 读路径（`list_messages`、`get_session`）无写锁，依赖 SQLite WAL 下读已提交快照；调用方不应对读结果做跨线程长期缓存后不经刷新再写。
- `update_session_status`：若 `session_id` 不存在则抛出 `SessionNotFoundError`（在写锁内先 `SELECT` 校验）。
- `replace_session_messages`：任一步失败时 `rollback`，避免连接长时间停留在未提交变更状态。

#### 1c. 压缩摘要与失败标记的边界语义（Hermes 风格）

- 写入会话的 compaction 摘要 / 静态 fallback / compaction 管线失败说明，均包装为 **REFERENCE ONLY** 说明 + 正文 + **END OF CONTEXT SUMMARY** 结束行，降低模型把历史摘要当作新用户指令的概率。
- 连续 compaction 失败时，**更新**同一条失败 system 标记（按尾部扫描匹配 `Automatic context compaction failed`），避免失败重试导致 system 噪声无限增长。

#### 1d. 高密度 tool 会话与 `max_tool_chains_pulled_into_tail`

- `ContextConfig.max_tool_chains_pulled_into_tail`（可选，`None` 表示不限制）：从**靠近尾部**一侧计数，最多将多少条 `assistant(tool_calls)+tool` 链纳入「左扩 tail」保护；更早的链可留在 middle 参与摘要，避免 middle 被工具链完全挤没而导致压缩无法收敛。与 Phase1 tool 占位、`sanitize_tool_pairs` 共同构成可压缩性与 API 合法性之间的折中。

### 2. Token 计算的准确性（分层，对齐 Hermes-agent 思路）

参考 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的《Context Compression and Caching》与 `ContextEngine` / `ContextCompressor` 设计：**不把「整窗长度」绑死在单一 tiktoken 调用上**，而是分层维护「预算信号」与「真实用量」：

1. **粗估层（gateway / 预检类比）**：在进入本轮主循环或加载会话时，可用 **字符启发式**（如 `estimate_messages_tokens_rough`）做低成本压力判断；阈值可配置得**高于**主压缩阈值（Hermes 中文档为网关卫生 ~85% vs 主压缩器默认 ~50%，EduAgent A2 无独立 Gateway 时可将「预检」放在 `ContextManager.load_context` 或每轮 `run_turn` 开头，仅作告警或提前触发压缩，不替代下面第 2 层）。
2. **主预算层（agent 内环）**：`context_token_limit`（如 model_max_tokens 的 60%）上的决策以 **tiktoken（适用模型）+ 启发式兜底** 为主；**每当 LLM 返回带 `usage.prompt_tokens`（或等价字段）时，用其校准/覆盖粗估**（与 Hermes 中 compressor 使用 API 上报 token 的思路一致）。

对非 OpenAI 模型（DeepSeek、Ollama），tiktoken 无可靠 encoding 时仍用字符启发式；后续可按模型插件化。

### 3. 压缩时调用 LLM

生成摘要时需要调用 LLM（通常用当前对话的 provider），这会产生额外 API 成本。建议：

- 仅在 token 严重超出（> 1.2 × limit）时才触发摘要。
- 摘要可缓存（hash(messages_content) 作为 key），避免重复调用。
- 提供配置项 `compression_enabled: bool`，允许用户关闭。

错误处理约束（与 Hermes-agent `ContextCompressor` 对齐，**已修订**）：

- 摘要 LLM 调用失败（网络、限流、无辅助模型、上下文长度报错等）时：**不终止整轮对话**；在已裁剪/合并消息边界的前提下，**插入一条固定的「摘要不可用」说明消息**（类似 Hermes 在 `summary` 为空时写入的 static fallback context marker），并 **打日志**（含 `last_summary_error` 语义的可查询字段可选写入 session 元数据），使模型与用户可知「中间轮次被删除但未成功摘要」而非静默丢失。
- **禁止**用本地启发式「编造」被删内容的摘要（仍不设本地 LLM fallback 生成真实摘要）；允许的是 **显式降级标记**，与 Hermes 文档中「无摘要则插入说明、仍继续会话」一致。
- 可选：对连续摘要失败实现 **冷却时间**（cooldown），避免在 provider 故障时每轮狂重试（实现细节见代码，配置项可放入 `ContextConfig`）。

### 4. 会话 ID 生成

使用 `uuid.uuid4()` 或 `shortuuid`，确保全局唯一。不使用时间戳或序列号（易重复）。

### 5. 向后兼容性

A2 会替换 JSONL，但为了平滑过渡，可选实现"迁移工具"将旧 JSONL 导入到 SQLite。建议先标记 JSONL 目录为 `_legacy_sessions`，不再向其中写入。

## 验收标准

### 会话管理

- 使用 `edu chat --session-id <id>` 可以恢复已有会话，消息顺序、工具调用状态精确一致。
- 使用 `edu list-sessions` 可看到按时间倒序的最近 20 个会话，展示标题/状态。
- 会话可被标记为归档，归档后只读。

### 上下文管理

- 单会话消息总 token 数超过限制时，自动触发压缩；若摘要 LLM 失败，须 **可见降级**（静态说明消息 + 日志），**不得**在无提示的情况下假装全量历史仍在；不得用本地启发式伪造被删内容的摘要。
- 压缩后最新 3 轮对话保留完整，中间对话替换为一条摘要消息。
- 摘要消息包含关键信息，Agent 可在后续对话中参考。

### 存储与查询

- SQLite 中的数据通过 SQL 可查询，支持 where 子句按用户、状态、时间范围等过滤。
- 并发 append_message 调用（同一 session）不会产生数据混乱或错误。
- Session 创建、消息追加、状态转换都有时间戳记录。

### 数据完整性

- 测试环境中删除 sessions.db 后重新启动，新会话创建成功。
- 工具调用的所有中间步骤（request + result）都被记录。

## 本阶段不做

- 不做分布式 session store（多进程 / 多机器共享），那是 A5。
- 不做 session 同步到云端备份。
- 不做 session 权限隔离（用户之间的会话查询约束），那是 B1。
- 不做消息加密存储。

## 确认的开放点

### 1. 压缩算法是否需要本地 LLM fallback？

当前方案是：调用当前 session 的 provider 生成摘要。如果无 API key 或 provider 离线，摘要调用会失败。

> **已修订**：仍 **不**引入单独本地模型去「生成真实摘要」；摘要失败时采用 **Hermes-agent 式降级**——保留头/尾与工具链边界策略的结果，插入 **静态 fallback 说明消息** 并记录日志（可选 cooldown），**会话继续**，与「失败即整轮抛错」的旧约定不同。

### 2. 会话过期策略

当前方案：会话 IDLE 后仍可恢复。若要自动删除过期会话（30 天未操作），是否需要？

> A2 先不做自动删除。提供手工 CLI 命令 `edu cleanup-sessions --before <date>` 供用户选择清理。
