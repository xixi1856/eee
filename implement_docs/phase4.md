# Phase 4：A4 工具运行时、工具集权限与 MCP 集成详细方案

## 目标与背景

A3 完成后，EduAgent 已经拥有完整的记忆系统与个性化学习者画像。A4 的核心目标是建立**完善的工具生态与运行时**，使 Agent 能够：

1. **规范化工具运行时**：定义工具的参数校验、结果格式化、耗时统计、错误处理的统一框架。
2. **按场景启用不同工具集**：定义"核心工具集""RAG工具集""行政工具集"等，支持动态启用/禁用。
3. **工具权限控制**：对文件写、网络请求、外部系统调用等危险操作加权限分级与审核提示。
4. **MCP 集成**：标准化接入第三方 MCP server，将其工具动态注册到 Agent 的工具库。
5. **多源 RAG 查询**：完善 `knowledge_query` 工具，支持个人 RAG + 课程 RAG 的统一接口。

A4 完成后，EduAgent 应该具备以下特征：

- 工具都通过统一的 runtime 框架运行，参数校验、错误处理、日志记录一致。
- 可通过配置文件启用/禁用工具集，无需修改代码。
- 危险操作（文件写、网络、执行命令）需要用户确认或权限豁免。
- 可连接标准 MCP server（stdio 或 HTTP），Agent 能直接调用其中的工具。
- `knowledge_query` 可同时查询个人 RAG 和课程 RAG，并清晰标注来源。

## 架构决策

### 决策 1：工具运行时的统一抽象

定义 `ToolRuntime` 类作为所有工具的执行引擎：

```python
class ToolRuntime:
    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        context: TurnRuntimeContext,
    ) -> ToolResult:
        """
        1. 查询工具定义，获取 schema
        2. 参数校验
        3. 权限检查
        4. 耗时统计开始
        5. 调用工具 handler
        6. 结果截断与格式化
        7. 耗时统计结束，记录日志
        8. 返回 ToolResult
        """
```

**好处**：

- 所有工具的 error handling、logging、metrics 一致。
- 可在此层统一做 timeout、rate limiting、retry。
- 后续升级到 distributed tracing 或 monitoring 时只需改这一处。

### 决策 2：Toolset 注册表与启用机制

定义 `ToolsetRegistry`：

```python
class ToolsetRegistry:
    core_toolset: Toolset        # remember_fact, search_memory, update_profile
    rag_toolset: Toolset         # knowledge_query
    web_toolset: Toolset         # web_search, browser_control
    file_toolset: Toolset        # read_file, write_file, list_dir
    eval_toolset: Toolset        # evaluate_hypothesis, rate_response
    delegation_toolset: Toolset  # delegate_to_subagent
    scheduling_toolset: Toolset  # schedule_task
    mcp_toolset: Toolset         # 动态加载的 MCP tools
```

每个 `Toolset` 包含：

```python
class Toolset(BaseModel):
    name: str
    enabled: bool
    tools: list[ToolDefinition]
    permissions: ToolPermissions  # read/write/network/execute 等
    cost_estimate: dict  # {api_calls, estimated_tokens, ...}
```

通过 `edu_agent.yaml` 配置启用/禁用：

```yaml
toolsets:
  core:
    enabled: true
  rag:
    enabled: true
  web:
    enabled: true
  file:
    enabled: false  # 禁用文件访问
  mcp:
    enabled: true
    servers:
      - uri: "stdio"
        command: "python -m mcp_servers.notion"
      - uri: "http"
        url: "http://localhost:3000/mcp"
```

### 决策 3：工具权限分级

定义 `ToolPermission` 枚举：

```python
class ToolPermission(Enum):
    READ = "read"              # 只读
    WRITE = "write"            # 创建/修改/删除
    NETWORK = "network"        # 网络请求
    EXECUTE = "execute"        # 执行命令/代码
    EXTERNAL = "external"      # 调用外部系统 API
```

每个工具定义其所需权限：

```python
class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict  # JSON Schema
    permissions: list[ToolPermission]
    approval_required: bool  # 是否需要用户确认
    max_timeout_sec: int
    max_output_tokens: int
    cost_estimate: dict  # 预估 API 成本
```

在 CLI 模式下，写操作需要用户确认：

```
Agent 想要执行：write_file(path="/data/output.txt")
权限需求：WRITE
是否允许? [y/n]
```

### 决策 4：多源 RAG 查询的统一接口

`knowledge_query` 工具支持两种来源：

```python
async def knowledge_query(
    query: str,
    sources: Literal["personal", "course", "all"] = "all",
    course_id: str | None = None,
) -> list[QueryResult]:
    """
    sources="personal": 仅查个人 RAG（本地 JSON/JSONL）
    sources="course": 仅查课程 RAG（PostgreSQL，需要 course_id）
    sources="all": 同时查两者，合并结果
    
    返回：
    [
        {origin: "personal", chunk_id, text, metadata},
        {origin: "course", chunk_id, text, metadata, course_id},
    ]
    """
```

**实现架构**：

- **个人 RAG**：使用当前 `rag_mvp` 的 LightRAG JSON 后端。Agent 侧调用时直接在本地查询。
- **课程 RAG**：存储于 PostgreSQL，有独立的 LightRAG namespace 表（`course_{course_id}_chunks` 等）。Agent 侧需要通过 API 或直连 PostgreSQL 查询。

当 session context 包含 `course_id` 时，优先查课程 RAG；无 `course_id` 时仅查个人 RAG。

### 决策 5：MCP 标准化集成

MCP（Model Context Protocol）定义了标准的工具调用接口。A4 支持通过 MCP 动态加载工具。

MCP client 的生命周期：

```
1. CLI 启动，加载 edu_agent.yaml 中的 mcp_servers 配置
2. 为每个 MCP server 创建 MCPClient（stdio 或 HTTP）
3. 连接 MCP server，获取其 tools list
4. 将 MCP tools 转换为 ToolDefinition，注册到 ToolsetRegistry
5. Agent 调用 MCP tool 时，通过 MCPClient.call_tool() 转发
6. 返回结果给 Agent
```

命名约束：

- 所有 MCP 动态工具注册时统一加命名空间前缀：`mcp.<server_name>.<tool_name>`。
- Agent 对模型暴露时可显示别名，但内部执行标识必须保持全名，避免与本地工具冲突。

### 决策 6：工具调用的结果截断与格式化

每个工具定义 `max_output_tokens`（默认 2000）。结果超出时自动截断：

```python
result = tool_handler(...)  # 返回 str 或 dict
truncated_result = truncate_and_format(
    result,
    max_tokens=tool_definition.max_output_tokens,
    format="text" | "json" | "markdown",
)
```

**不采用简单字符串截断**的原因：

- JSON 截断容易产生无效结构。
- 关键信息可能在尾部（如"结果是正确的"）。
- 应该智能提取摘要而非粗暴截断。

### 决策 7：工具调用链与出错重试

`ToolRuntime.execute()` 中包含基础重试机制：

```python
# 对临时错误自动重试（网络超时、API 限流）
# 对永久错误（权限不足、工具不存在）直接失败
# 对用户拒绝操作（如 "不允许写文件"），返回拒绝消息给 Agent

retryable_errors = {TimeoutError, RateLimitError, ...}
max_retries = 3
backoff_base = 1.5  # 指数退避

for attempt in range(max_retries):
    try:
        return await _execute_tool_handler(...)
    except retryable_errors as e:
        if attempt < max_retries - 1:
            await asyncio.sleep(backoff_base ** attempt)
        else:
            raise
```

## 文件清单

### 新建文件

- [src/edu_agent/toolsets/__init__.py](e:/appProjects/eee/src/edu_agent/toolsets/__init__.py)
  职责：toolsets 子包导出入口。

- [src/edu_agent/toolsets/models.py](e:/appProjects/eee/src/edu_agent/toolsets/models.py)
  职责：定义 `ToolDefinition`、`Toolset`、`ToolPermission`、`ToolResult`、`ToolsConfig`。

- [src/edu_agent/toolsets/registry.py](e:/appProjects/eee/src/edu_agent/toolsets/registry.py)
  职责：`ToolsetRegistry` 类，管理所有 toolsets 的注册、启用/禁用、查询。

- [src/edu_agent/toolsets/runtime.py](e:/appProjects/eee/src/edu_agent/toolsets/runtime.py)
  职责：`ToolRuntime` 类，提供：
  - `async execute(tool_name, arguments, context)`
  - 参数校验、权限检查、超时控制、重试、结果截断。

- [src/edu_agent/toolsets/permissions.py](e:/appProjects/eee/src/edu_agent/toolsets/permissions.py)
  职责：权限检查逻辑，提供：
  - `check_permission(tool_name, permission) -> bool`
  - `request_approval(tool_name, arguments) -> bool`（CLI 交互或配置豁免）

- [src/edu_agent/toolsets/result_formatter.py](e:/appProjects/eee/src/edu_agent/toolsets/result_formatter.py)
  职责：结果格式化与截断，提供：
  - `format_and_truncate(result, max_tokens, format)`

- [src/edu_agent/mcp/__init__.py](e:/appProjects/eee/src/edu_agent/mcp/__init__.py)
  职责：MCP 子包导出入口。

- [src/edu_agent/mcp/client.py](e:/appProjects/eee/src/edu_agent/mcp/client.py)
  职责：`MCPClient` 基类与两个实现：
  - `StdioMCPClient`：通过 subprocess stdio 与 MCP server 通信。
  - `HttpMCPClient`：通过 HTTP 与 MCP server 通信。
  - 统一接口：`list_tools() -> list[ToolDefinition]`、`call_tool(name, args)`

- [src/edu_agent/mcp/loader.py](e:/appProjects/eee/src/edu_agent/mcp/loader.py)
  职责：MCP server 的加载与生命周期管理，提供：
  - `load_mcp_servers(mcp_configs) -> list[MCPClient]`
  - `shutdown_mcp_servers()`

- [src/edu_agent/tools/knowledge_query.py](e:/appProjects/eee/src/edu_agent/tools/knowledge_query.py)
  职责：重新实现 `knowledge_query` 工具，支持多源查询与结果融合。
  - 调用 rag_mvp 查询个人 RAG
  - 调用 PostgreSQL 查询课程 RAG（后续 B2）
  - 标注 `origin` 并返回

- [tests/edu_agent/test_toolset_runtime.py](e:/appProjects/eee/tests/edu_agent/test_toolset_runtime.py)
  职责：验证工具执行、参数校验、权限检查、结果截断、重试。

- [tests/edu_agent/test_mcp_integration.py](e:/appProjects/eee/tests/edu_agent/test_mcp_integration.py)
  职责：验证 MCP 客户端的工具加载与调用（使用 mock MCP server）。

### 修改文件

- [src/edu_agent/types.py](e:/appProjects/eee/src/edu_agent/types.py)
  变更：`AgentConfig` 新增 `toolsets_config: ToolsConfig` 字段。

- [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py)
  变更：
  - 构造函数新增 `runtime: ToolRuntime | None` 注入
  - `run_turn()` 中调用工具时改为使用 `runtime.execute()` 而非直接调用 handler
  - 删除旧的"provider 分支判断"的工具逻辑（现在统一由 runtime 负责）

- [src/edu_agent/tools/eval.py](e:/appProjects/eee/src/edu_agent/tools/eval.py)
  变更：
  - 改为 async handler（与 runtime 框架兼容）
  - 不再包含权限检查、retry、timeout 逻辑（那是 runtime 的职责）
  - 仅实现核心评测逻辑

- [src/edu_agent/tools/search.py](e:/appProjects/eee/src/edu_agent/tools/search.py)
  变更：同上，改为 async handler，删除运行时相关逻辑。

- [src/edu_agent/tools/files.py](e:/appProjects/eee/src/edu_agent/tools/files.py)（若存在）
  变更：定义 WRITE 权限，改为 async handler。

- [src/edu_agent/tools/__init__.py](e:/appProjects/eee/src/edu_agent/tools/__init__.py)
  变更：导出所有工具 handler。

- [src/edu_agent/cli.py](e:/appProjects/eee/src/edu_agent/cli.py)
  变更：
  - 加载 `edu_agent.yaml` 时同时加载 toolsets 配置
  - 初始化 `ToolsetRegistry`、`ToolRuntime`、`MCPClient` 等
  - 新增 `--disable-tool <toolname>` 参数
  - 新增 `list-tools` 子命令，展示启用的工具

- [README.md](e:/appProjects/eee/README.md)
  变更：补充工具集配置、权限审批、MCP 接入的说明。

## 接口契约

### 1. ToolRuntime

```python
class ToolRuntime:
    def __init__(
        self,
        registry: ToolsetRegistry,
        settings: EduSettings,
        permissions_checker: PermissionsChecker,
    ) -> None: ...
    
    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        context: TurnRuntimeContext,
    ) -> ToolResult: ...
```

### 2. MCPClient

```python
class MCPClient(ABC):
    @abstractmethod
    async def list_tools(self) -> list[ToolDefinition]: ...
    
    @abstractmethod
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
    ) -> str: ...

class StdioMCPClient(MCPClient):
    def __init__(self, command: str, args: list[str]) -> None: ...

class HttpMCPClient(MCPClient):
    def __init__(self, base_url: str) -> None: ...
```

### 3. knowledge_query 的返回格式

```python
class QueryResult(BaseModel):
    chunk_id: str
    text: str
    origin: Literal["personal", "course"]
    course_id: str | None  # 仅当 origin="course" 时有值
    document_title: str | None
    relevance_score: float  # 0.0 - 1.0

async def knowledge_query(
    query: str,
    sources: Literal["personal", "course", "all"] = "all",
    course_id: str | None = None,
) -> list[QueryResult]: ...
```

## 实施顺序

1. 定义 ToolDefinition、Toolset 等数据模型。
2. 实现 ToolsetRegistry，支持 CRUD 与启用/禁用。
3. 实现 ToolRuntime，集成参数校验、权限检查、超时、重试。
4. 实现 MCP 客户端与加载器。
5. 改造现有工具为 async handlers，删除运行时相关代码。
6. 改造 Agent.run_turn() 使用 ToolRuntime。
7. 重新实现 knowledge_query 支持多源。
8. 补充测试与 CLI 命令。

## 注意事项

### 1. Async / Await 对 Agent 的影响

当前 Agent 和工具基于同步调用。A4 改为 async，需要改造 `Agent.run_turn()` 为异步方法。

对 CLI 的影响：需要在 CLI 中使用 `asyncio.run()` 来调用异步 Agent。

### 2. MCP 与 OpenAI tool_calling 的适配

OpenAI 的 tool_calling schema 与 MCP 的 tool schema 有细微差异。需要实现转换器：

```python
def mcp_tool_to_openai_tool(mcp_tool: ToolDefinition) -> dict:
    # 将 MCP ToolDefinition 转换为 OpenAI function schema
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description,
            "parameters": mcp_tool.parameters,
        }
    }
```

### 3. 工具权限的存储与配置

权限配置可以有多种方式：

- **配置文件**：`edu_agent.yaml` 中硬编码权限决策（推荐，简单）。
- **CLI 交互**：每次尝试危险操作时询问用户（推荐用于开发/测试）。
- **预设策略**：如"教师模式"自动允许所有操作（后续 B1）。

A4 实现前两种。

### 4. 课程 RAG 的 PostgreSQL 连接

A4 中 `knowledge_query` 支持课程 RAG 查询，但课程 RAG 的 PostgreSQL 表还未创建（那是 B2）。

A4 可以预留接口，具体实现延后到 B2。或者在 A4 测试中 mock PostgreSQL 响应。

## 验收标准

### 工具运行时

- 所有工具调用经过 ToolRuntime，支持参数校验。
- 工具超时后自动失败，不卡 Agent。
- 网络临时错误自动重试 3 次。

### Toolset 管理

- 可通过 `edu_agent.yaml` 启用/禁用工具集。
- 禁用的工具不出现在 Agent 的可用工具列表中。
- `edu list-tools` 可查看所有启用的工具。

### 权限控制

- 文件写操作在 CLI 模式下需要用户确认。
- 确认过的操作不重复询问。
- `--approve-all` 标志可跳过所有确认（用于非交互模式）。

### MCP 集成

- 可配置一个 stdio MCP server，Agent 能列出并调用其工具。
- MCP 工具的错误被正确捕获和报告。

### 多源 RAG

- `knowledge_query` 可查询个人 RAG 并返回带 `origin="personal"` 的结果。
- 测试中 mock 课程 RAG 返回，验证结果融合逻辑。

## 本阶段不做

- 不做工具参数的动态优化（如"自动调整 max_tokens"）。
- 不做工具调用的并行执行（Agent 框架不支持）。
- 不做 MCP 的连接池或负载均衡。
- 不做课程 RAG 的真实实现，那是 B2。

## 确认的开放点

### 1. 工具调用的序列化格式

当前 OpenAI schema 使用 `tool_calls[].function.arguments` 为 JSON string。

是否需要在 ToolRuntime 中自动 deserialize arguments，还是让 handler 自己做？

> 建议：ToolRuntime 做 deserialize + JSON Schema validation，handler 只接收 dict。

### 2. MCP 工具与本地工具的命名冲突

如果 MCP server 提供的工具名与本地工具重名（都叫 `web_search`），如何处理？

> 已确认：MCP 工具自动加前缀并包含 server 名（如 `mcp.notion.web_search`），避免跨 server 冲突。
