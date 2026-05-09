# Phase 1：A1 配置与 Provider 运行时详细方案

## 目标与背景

本阶段目标不是简单把配置从 [src/rag_mvp/config.py](e:/appProjects/eee/src/rag_mvp/config.py) 挪到 EduAgent，而是完成三件事：

1. 建立 EduAgent 自己的配置体系，彻底解除 [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py) 、 [src/edu_agent/subagent.py](e:/appProjects/eee/src/edu_agent/subagent.py) 、 [src/edu_agent/tools/eval.py](e:/appProjects/eee/src/edu_agent/tools/eval.py) 、 [src/edu_agent/tools/search.py](e:/appProjects/eee/src/edu_agent/tools/search.py) 对 `rag_mvp.config.settings` 的运行时依赖。
2. 建立 provider 注册表与运行时解析层，为后续 A2 的上下文压缩、A4 的 MCP 和多 toolset、A5 的 Gateway 多入口复用同一套 provider 机制打基础。
3. 建立统一路径体系和 runtime 注入方式，避免后续继续出现“模块内部偷偷读环境变量 / 全局 settings”的扩散。

本阶段完成后，EduAgent 应该具备以下结构特征：

- CLI 入口负责加载配置并装配 runtime。
- Agent、SubAgent、tool handler 只依赖显式注入的 runtime 或当前 turn 的 runtime context。
- provider 选择、api_key/base_url/model 的解析由统一 runtime 层处理。
- rag_mvp 仍保留自己的配置体系，作为独立 RAG Worker 使用；EduAgent 不再直接依赖它。

## 架构决策

### 决策 0：参考实现对齐边界（nanobot / hermes-agent）

本阶段参考 nanobot 与 hermes-agent 的实现经验，但保持最小可落地范围：

- 对齐点 1：入口装配 + 运行时注入。配置加载只在入口发生，运行中通过显式 context 注入。
- 对齐点 2：provider 解析层与业务逻辑解耦，避免在 Agent 与工具中散落 provider 分支。
- 对齐点 3：不在 A1 引入重型网关编排能力（中断协议、命令路由、复杂会话键），这些留给 A5。

这样做的目标是：先把 A1 做成可测试、可扩展的地基，而不是一次性复制参考项目的全部复杂度。

### 决策 1：配置采用 Root Settings + 分区子配置

采用类似 nanobot 的 root schema 结构，但只先落地 A1 所需的最小集合，避免一开始把 A4/A5 未来字段一次性堆满。

根配置对象建议命名为 `EduSettings`，至少包含以下分区：

- `agent`: 默认模型、provider、temperature、max_tokens、workspace、skills_dir、max_iterations
- `providers`: provider 配置映射，按 canonical provider id 存储
- `tools`: web search、proxy、evaluation、mcp 预留位
- `runtime`: 日志级别、默认时区、环境标志

不在本阶段加入 `channels`、`gateway`、`memory` 的完整细节，但要预留字段结构，避免 A5 时再做破坏性重排。

### 决策 2：AgentConfig 继续保留，但语义改为“会话级覆盖项”

当前 [src/edu_agent/types.py](e:/appProjects/eee/src/edu_agent/types.py) 中的 `AgentConfig` 混合了三类信息：

- 全局默认配置
- 会话级配置
- 存储路径配置

这会导致配置边界混乱。

A1 中 `AgentConfig` 不再作为“全局配置容器”，而改为轻量的 session/runtime overrides，只保留：

- `user_id`
- `session_id`
- `model`
- `provider`
- `workspace`
- `skills_dir`（可选 override）
- `max_iterations`（可选 override）

`profile_storage_dir`、`session_storage_dir` 这类路径字段从 `AgentConfig` 移除，统一交给 `EduPaths` 和 `EduSettings.agent.workspace` 推导。

这不是向后兼容设计，而是边界纠正。测试需要同步改写。

### 决策 3：Provider Registry 是唯一 provider 元数据来源

建立 `edu_agent.providers.registry`，集中维护 canonical provider spec：

- provider id
- 默认 base_url
- api mode
- 默认模型
- 认证字段名
- 是否 OpenAI-compatible
- 是否支持 streaming
- 是否支持 tool calling

不允许 [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py) 、 [src/edu_agent/subagent.py](e:/appProjects/eee/src/edu_agent/subagent.py) 、 [src/edu_agent/tools/eval.py](e:/appProjects/eee/src/edu_agent/tools/eval.py) 中再出现 provider 分支判断。

### 决策 4：运行时解析独立于配置模型

配置模型只描述“声明值”，运行时解析层负责决定最终使用什么：

- 最终 provider
- 最终 model
- 最终 api_key
- 最终 base_url
- 最终 client 类型

建议新增 `ResolvedProviderRuntime` 作为解析后的只读对象，由 `resolve_provider_runtime(settings, overrides, purpose)` 返回。

`purpose` 先支持：

- `main`
- `subagent`
- `auxiliary`

虽然 A1 暂不真正实现 auxiliary client，但运行时接口要一次设计好，避免 A2 再拆。

### 决策 5：通过 ContextVar 注入当前 turn runtime

当前 tool handler 是全局函数，经由 registry 分发，没有构造器注入入口。如果 A1 只改 agent 和 subagent，不改工具的 runtime 获取方式，那么 [src/edu_agent/tools/eval.py](e:/appProjects/eee/src/edu_agent/tools/eval.py) 和 [src/edu_agent/tools/search.py](e:/appProjects/eee/src/edu_agent/tools/search.py) 仍会继续偷读全局配置。

因此 A1 必须新增一个轻量 runtime context 机制：

- 进入 `EduAgent.run_turn()` 前设置当前 runtime context
- tool handler 内通过 `get_current_runtime()` 读取 settings、provider runtime、paths、user/session 元信息
- turn 结束后清理 context

这个方案比“把 settings 做成新的全局单例”更可扩展，也更符合后续 Gateway 并发 session 的方向。

### 决策 6：不再从 rag_mvp.config 回退读取配置

默认不做向后兼容。

A1 起 EduAgent 的主运行路径只认：

- `edu_agent.yaml`
- `.env`
- 显式传入的 `EduSettings`

不再允许“如果没配就去读 rag_mvp.config.settings”。

原因：

- 这会继续维持错误的模块依赖方向
- 后续平台化后，EduAgent 与 rag_mvp 是两个独立服务，不应共享一个配置单例
- fallback 会让问题拖到 A4/A5 才爆出来

## 文件清单

### 新建文件

- [src/edu_agent/config.py](e:/appProjects/eee/src/edu_agent/config.py)
  职责：定义 `EduSettings`、`AgentDefaults`、`ProviderConfig`、`ProvidersSettings`、`ToolsSettings`、`RuntimeSettings`。
  说明：采用 Pydantic Settings，支持从 `edu_agent.yaml` + `.env` + 环境变量加载。

- [src/edu_agent/paths.py](e:/appProjects/eee/src/edu_agent/paths.py)
  职责：定义 `EduPaths`，统一推导 workspace、sessions、profiles、memory、skills、logs、cache 等目录。
  说明：所有路径从 `workspace` 根推导，不允许各模块自己拼路径字面量。

- [src/edu_agent/config_loader.py](e:/appProjects/eee/src/edu_agent/config_loader.py)
  职责：提供 `load_settings()`、`load_settings_from_file()`、`resolve_env_vars()`、基础校验逻辑。
  说明：CLI、未来 API server、未来 gateway 统一通过这里装载配置。

- [src/edu_agent/providers/__init__.py](e:/appProjects/eee/src/edu_agent/providers/__init__.py)
  职责：provider 子包导出入口。

- [src/edu_agent/providers/types.py](e:/appProjects/eee/src/edu_agent/providers/types.py)
  职责：定义 `ProviderSpec`、`ResolvedProviderRuntime`、`ProviderPurpose`。

- [src/edu_agent/providers/registry.py](e:/appProjects/eee/src/edu_agent/providers/registry.py)
  职责：维护 canonical provider registry。
  第一批至少覆盖：`openai`、`deepseek`、`ollama`、`dashscope`、`openai_compatible`。

- [src/edu_agent/providers/runtime.py](e:/appProjects/eee/src/edu_agent/providers/runtime.py)
  职责：provider 归一化、模型覆盖、base_url 解析、api_key 解析、client 构造。
  说明：对外只暴露纯函数和只读运行时对象。

- [src/edu_agent/providers/retry.py](e:/appProjects/eee/src/edu_agent/providers/retry.py)
  职责：定义基础重试策略、可重试错误分类、退避策略。
  说明：A1 不做多 credential pool，只做单 credential 的基础 retry/fail-fast。

- [src/edu_agent/runtime_context.py](e:/appProjects/eee/src/edu_agent/runtime_context.py)
  职责：基于 `ContextVar` 保存当前 turn 的 runtime context，并提供 `set_current_runtime()` / `get_current_runtime()`。

- [tests/edu_agent/test_config_loader.py](e:/appProjects/eee/tests/edu_agent/test_config_loader.py)
  职责：验证 yaml/.env/环境变量优先级、默认值、路径展开。

- [tests/edu_agent/test_provider_runtime.py](e:/appProjects/eee/tests/edu_agent/test_provider_runtime.py)
  职责：验证 provider alias、base_url override、model override、client 构造和 retry 分类。

### 修改文件

- [src/edu_agent/types.py](e:/appProjects/eee/src/edu_agent/types.py)
  变更：重定义 `AgentConfig` 的语义，移除路径类字段，加入 `provider`、`workspace` 等 override 字段。
  注意：这是破坏性修改，测试要同步改。

- [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py)
  变更：
  - 构造函数增加 `settings: EduSettings` 或 `runtime_factory` 注入
  - 删除对 `rag_mvp.config.settings` 的懒加载
  - 通过 provider runtime 创建 main client
  - 通过 `EduPaths` 推导 profile/session/skills 路径
  - `run_turn()` 期间设置 runtime context，供工具读取

- [src/edu_agent/subagent.py](e:/appProjects/eee/src/edu_agent/subagent.py)
  变更：
  - 删除对 `rag_mvp.config.settings` 的依赖
  - 使用与主 Agent 相同的 provider runtime 解析器，但 `purpose=subagent`
  - 支持从主 runtime 继承 settings 和 provider overrides

- [src/edu_agent/cli.py](e:/appProjects/eee/src/edu_agent/cli.py)
  变更：
  - CLI 启动时先加载 `EduSettings`
  - 构造 `EduPaths`
  - 再创建 `EduAgent`
  - 增加配置文件路径参数预留（可先不暴露完整命令，但内部结构要支持）

- [src/edu_agent/tools/eval.py](e:/appProjects/eee/src/edu_agent/tools/eval.py)
  变更：
  - `_call_llm()` 不再读取 `rag_mvp.config.settings`
  - 改为从 runtime context 获取当前 auxiliary/main provider runtime
  - 保持测试可 patch 的入口不变，即 `_call_llm()` 仍为模块级函数

- [src/edu_agent/tools/search.py](e:/appProjects/eee/src/edu_agent/tools/search.py)
  变更：
  - Tavily key、proxy 等从 runtime context.settings.tools 读取
  - 不再局部 import `rag_mvp.config.settings`

- [src/edu_agent/session_store.py](e:/appProjects/eee/src/edu_agent/session_store.py)
  变更：默认存储目录改由 `EduPaths.sessions_dir` 注入，不再使用模块内 `_DEFAULT_STORAGE` 作为主路径来源。
  说明：A2 会完全替换该模块；A1 只先消掉路径硬编码。

- [src/edu_agent/learner_profile.py](e:/appProjects/eee/src/edu_agent/learner_profile.py)
  变更：默认存储目录改由 `EduPaths.profiles_dir` 注入，不再依赖模块内 `_DEFAULT_STORAGE` 作为主路径来源。

- [tests/edu_agent/test_agent_loop.py](e:/appProjects/eee/tests/edu_agent/test_agent_loop.py)
  变更：
  - 不再 patch `rag_mvp.config.settings`
  - 改为传入测试专用 `EduSettings`
  - 验证 Agent 通过 settings/runtime 构造 client

- [tests/edu_agent/test_cli_chat.py](e:/appProjects/eee/tests/edu_agent/test_cli_chat.py)
  变更：CLI 的断言从“只看 AgentConfig”扩展为“先加载 settings，再构造 Agent”。

- [tests/edu_agent/test_subagent.py](e:/appProjects/eee/tests/edu_agent/test_subagent.py)
  变更：SubAgent 初始化路径改为 runtime 注入，更新 fixture 与断言。

- [tests/edu_agent/test_tools_phase3.py](e:/appProjects/eee/tests/edu_agent/test_tools_phase3.py)
  变更：补充 runtime context fixture，确保 `tools/eval.py` 在无全局 settings 情况下也能工作。

- [README.md](e:/appProjects/eee/README.md)
  变更：补充 EduAgent 独立配置说明、配置文件位置与基础示例。

### 明确保留但本阶段不改

- [src/rag_mvp/config.py](e:/appProjects/eee/src/rag_mvp/config.py)
  原因：rag_mvp 作为独立 RAG Worker 仍需要自己的配置系统；A1 的目标是解耦，不是合并两个包的配置体系。

- [src/edu_agent/tools_legacy.py](e:/appProjects/eee/src/edu_agent/tools_legacy.py)
  原因：非主路径，不纳入 A1 主改造范围。
  注意：如果后续仍被运行路径触发，A4 统一工具运行时阶段再清理或删除。

## 接口契约

### 1. 根配置

```python
class EduSettings(BaseModel):
    agent: AgentDefaults
    providers: ProvidersSettings
    tools: ToolsSettings
    runtime: RuntimeSettings
```

### 2. AgentDefaults

```python
class AgentDefaults(BaseModel):
    workspace: Path
    model: str
    provider: str
    temperature: float
    max_tokens: int
    max_iterations: int
    skills_dir: str
```

### 3. ProviderSpec

```python
class ProviderSpec(BaseModel):
    id: str
    aliases: list[str]
    default_base_url: str | None
    api_mode: Literal["chat_completions", "responses", "anthropic_messages"]
    client_kind: Literal["openai", "anthropic"]
    supports_streaming: bool
    supports_tool_calling: bool
```
```

### 4. 解析后的 provider runtime

```python
class ResolvedProviderRuntime(BaseModel):
    provider_id: str
    model: str
    api_key: str
    base_url: str | None
    api_mode: str
    client_kind: str
    temperature: float
    max_tokens: int
```
```

### 5. 配置加载入口

```python
def load_settings(config_path: Path | None = None) -> EduSettings
def build_paths(settings: EduSettings, overrides: AgentConfig | None = None) -> EduPaths
def resolve_provider_runtime(
    settings: EduSettings,
    overrides: AgentConfig | None,
    purpose: ProviderPurpose = "main",
) -> ResolvedProviderRuntime
```

### 6. Agent / SubAgent 构造签名

```python
class EduAgent:
    def __init__(self, config: AgentConfig | None = None, settings: EduSettings | None = None) -> None

class SubAgent:
    def __init__(
        self,
        model: str = "",
        client: OpenAI | None = None,
        settings: EduSettings | None = None,
        runtime: ResolvedProviderRuntime | None = None,
    ) -> None
```

说明：

- `settings` 可选仅用于测试便利；正式入口应由 CLI/API 先统一加载后传入。
- `client` 注入仅保留给测试。生产路径必须走 runtime resolver。

### 7. runtime context

```python
class TurnRuntimeContext(BaseModel):
    settings: EduSettings
    paths: EduPaths
    provider_runtime: ResolvedProviderRuntime
    user_id: str
    session_id: str
```

```python
def set_current_runtime(ctx: TurnRuntimeContext) -> Token
def reset_current_runtime(token: Token) -> None
def get_current_runtime() -> TurnRuntimeContext
```

## 实施顺序

1. 先落地 `config.py`、`config_loader.py`、`paths.py`、provider 子包。
2. 重构 [src/edu_agent/types.py](e:/appProjects/eee/src/edu_agent/types.py) 的 `AgentConfig`。
3. 改 [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py) 和 [src/edu_agent/subagent.py](e:/appProjects/eee/src/edu_agent/subagent.py) 的 client 构造与路径推导。
4. 补 runtime context，并修改 [src/edu_agent/tools/eval.py](e:/appProjects/eee/src/edu_agent/tools/eval.py) 与 [src/edu_agent/tools/search.py](e:/appProjects/eee/src/edu_agent/tools/search.py) 的配置读取方式。
5. 最后改 CLI 和测试。

这个顺序的原因是：先建立新的装配层，再迁移消费方，避免中途出现“双配置系统混用”。

## 注意事项

### 1. 不允许新增新的模块级 settings 单例

`load_settings()` 可以有缓存，但缓存对象只能在入口层持有并显式传入，不允许其他业务模块写出 `from edu_agent.config import settings` 这种新的全局读法。

### 2. `OpenAI()` 构造必须收敛到 provider runtime

当前 [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py) 、 [src/edu_agent/subagent.py](e:/appProjects/eee/src/edu_agent/subagent.py) 、 [src/edu_agent/tools/eval.py](e:/appProjects/eee/src/edu_agent/tools/eval.py) 都可能直接 new `OpenAI()`。

A1 完成后，生产代码中的 client 构造只能通过统一 helper 完成。测试中允许直接注入 mock client。

### 3. tools 只能读当前 turn context，不能自己 load config

这是 A5 并发 session 的前置约束。如果 tool 自己读取 yaml/env，会导致不同 session 之间无法隔离 provider/runtime。

### 4. 不在 A1 引入多 credential pool

Hermes 的 credential pool 很强，但当前项目还没有必要一开始就引入。

A1 只做：

- 单 provider 单 credential
- 基础 retry
- 明确错误分类

把复杂度留给后续 provider 增强阶段。

### 5. README 只更新必要使用方式，不写未来能力

本阶段 README 只说明当前可运行的配置结构和启动方式，不提前写 A4/A5 的 MCP/Gateway 描述，避免文档超前于实现。

### 6. 若必须保留兼容层，必须显式标记

允许的唯一兼容层是：

- `EduAgent(..., settings=...)` 和 `SubAgent(..., settings=...)` 这种测试友好入口

不允许保留：

- 从 `rag_mvp.config` fallback
- 旧路径字段和新路径体系并存
- 旧 provider 解析分支和新 registry 并存

## 验收标准

### 配置与路径

- 使用 `edu_agent.yaml` 可以切换 `openai`、`deepseek`、`ollama`、`dashscope`，不修改业务代码。
- 所有用户数据路径从 `workspace` 推导，项目中不再把 `session_logs`、`learner_profiles` 写死为默认主路径。
- [src/edu_agent](e:/appProjects/eee/src/edu_agent) 下除保留的非主线文件外，不再出现 `from rag_mvp.config import settings`。

### Agent 与工具

- [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py) 和 [src/edu_agent/subagent.py](e:/appProjects/eee/src/edu_agent/subagent.py) 能通过同一套 runtime resolver 创建 client。
- [src/edu_agent/tools/eval.py](e:/appProjects/eee/src/edu_agent/tools/eval.py) 和 [src/edu_agent/tools/search.py](e:/appProjects/eee/src/edu_agent/tools/search.py) 在没有 `rag_mvp.config.settings` 的前提下仍可运行。
- 单元测试中可以显式传入测试 settings，不依赖真实环境变量。

### 测试范围

至少通过以下测试切片：

- [tests/edu_agent/test_config_loader.py](e:/appProjects/eee/tests/edu_agent/test_config_loader.py)
- [tests/edu_agent/test_provider_runtime.py](e:/appProjects/eee/tests/edu_agent/test_provider_runtime.py)
- [tests/edu_agent/test_agent_loop.py](e:/appProjects/eee/tests/edu_agent/test_agent_loop.py)
- [tests/edu_agent/test_cli_chat.py](e:/appProjects/eee/tests/edu_agent/test_cli_chat.py)
- [tests/edu_agent/test_subagent.py](e:/appProjects/eee/tests/edu_agent/test_subagent.py)
- [tests/edu_agent/test_tools_phase3.py](e:/appProjects/eee/tests/edu_agent/test_tools_phase3.py)

### 人工验证

1. 准备一份最小 `edu_agent.yaml`，指定 `provider=dashscope`。
2. 启动 `edu chat`，确认会话可正常回复。
3. 修改配置切到 `ollama`，再次启动，无需改代码。
4. 调用 `hint_generator`、`score_essay`、`web_search`，确认工具仍能工作。
5. 检查日志，确认 provider 解析失败、缺失 api_key、base_url 无效时都有清晰错误信息。

## 本阶段不做

- 不做上下文压缩与 token 预算管理，那是 A2。
- 不做 SQLite session store 替换，那是 A2。
- 不做 memory consolidator，那是 A3。
- 不做 tool availability check、权限审批、MCP 动态注册，那是 A4。
- 不做 Gateway、MessageBus、HTTP API，那是 A5。

## 确认的开放点

A1 有一个实现层面的开放点，但不会阻塞写代码：

- `edu_agent.yaml` 放在仓库根目录，还是放在 `config/edu_agent.yaml`？

先用仓库根目录，原因是当前项目还没有独立的 `config/` 目录约定，CLI 和测试也更简单。后续如果做 `EDU_HOME` 初始化，再迁移到用户目录。