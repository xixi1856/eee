# Phase 5：A5 消息总线、SessionRunner 与 Gateway 详细方案

## 目标与背景

A4 完成后，EduAgent 已经拥有完善的工具生态与工具权限系统。A5 是 Agent 主线的最后一阶段，核心目标是建立**多 channel 的消息总线与 Gateway 架构**，使 Agent 能够：

1. **统一消息模型**：定义 InboundMessage 与 OutboundMessage，支持不同来源的消息统一处理。
2. **按 session 串行处理**：每个 session 的消息按序串行，避免数据竞争；不同 session 可并发处理。
3. **多 channel 接入**：通过 channel adapter 允许多种接入方式（CLI、WebSocket、HTTP API、未来的微信/飞书）。
4. **HTTP API Server**：提供 FastAPI 服务，暴露 `/v1/chat/completions`、`/sessions`、`/tools` 等标准接口，支持 SSE streaming。
5. **Gateway 进程**：长运行进程，负责 channel 路由、session 生命周期、授权等。

A5 完成后，EduAgent 应该具备以下特征：

- 可通过 HTTP 创建 session、发送消息、接收流式回复。
- WebSocket 可同时处理多个 session，session 内消息串行。
- CLI 作为独立 channel adapter 接入 Gateway 或独立运行。
- 新增一个 channel adapter 不需要修改 Agent 核心代码。
- 支持扩展到微服务架构（分离 Agent runner、RAG worker、API gateway）。

## 架构决策

### 决策 1：消息总线的消息模型

定义 `InboundMessage` 与 `OutboundMessage`：

```python
class InboundMessage(BaseModel):
    message_id: str  # UUID，唯一标识此消息
    channel: str  # "cli", "websocket", "http", ...
    session_id: str
    user_id: str
    timestamp: datetime
    content: str
    metadata: dict  # {ip, user_agent, source, ...}

class OutboundMessage(BaseModel):
    message_id: str
    in_reply_to: str  # inbound_message_id
    session_id: str
    user_id: str
    timestamp: datetime
    content: str  # 当前流水数据
    content_type: Literal["text", "tool_call", "tool_result", "error", "meta"]
    is_final: bool  # 流式回复的最后一条
    metadata: dict  # {tool_name, execution_time_ms, ...}
```

**不采用 OpenAI schema**的原因：

- OpenAI schema 是双向的（支持 tool_calling），A5 只需单向流（Agent → User）。
- 自定义 schema 可以更灵活地表示教学场景的特定信息（如"hint_level"、"resource_link"）。

### 决策 2：SessionRunner 与并发模型

定义 `SessionRunner` 负责：

```python
class SessionRunner:
    """
    为每个 session 维护一个独占的 runner instance，确保消息串行处理。
    跨 session 的 runners 可并发运行。
    """
    
    async def process_message(
        self,
        inbound: InboundMessage,
    ) -> AsyncGenerator[OutboundMessage, None]:
        """
        处理单条 inbound message，逐条产出 outbound messages（流式）。
        这是工作的核心：
        1. 加载或创建 session
        2. 加载上下文（memory、历史消息）
        3. 创建 Agent instance
        4. 运行 Agent.run_turn()
        5. 逐条 yield 回复
        6. 会话结束时触发 memory consolidation
        """
```

并发模型：

```
Gateway
  ├─ message_queue (按 session_id 路由)
  │
  ├─ SessionRunner[session_id_1]  (处理 session_id_1 的消息)
  │   └─ message_queue[session_id_1]
  │
  ├─ SessionRunner[session_id_2]  (处理 session_id_2 的消息)
  │   └─ message_queue[session_id_2]
  │
  └─ ...

特点：
- 同一 session 的消息严格FIFO处理
- 不同 session 的 runner 并发执行
- 通过 asyncio.Queue 与 Task 实现
```

补充中断协议（参考 hermes-agent 的运行经验）：

- 在 session busy 时，新消息默认入队，不直接抢占当前 turn。
- 预留控制消息类型：`interrupt`、`cancel`、`approve`、`deny`，由 Gateway 在 runner 边界处理。
- A5 先实现 `interrupt`（软中断）与 `cancel`（终止当前工具调用）两类基础控制。

### 决策 3：Channel Adapter 模式

定义 `ChannelAdapter` 基类：

```python
class ChannelAdapter(ABC):
    """
    将不同输入源（CLI、HTTP、WebSocket）的消息转换为 InboundMessage，
    并负责将 OutboundMessage 返回给用户。
    """
    
    @abstractmethod
    async def start(self) -> None: ...
    
    @abstractmethod
    async def stop(self) -> None: ...
```

示例实现：

```python
class HTTPChannelAdapter(ChannelAdapter):
    """FastAPI HTTP/SSE adapter"""
    def __init__(self, host: str, port: int) -> None: ...
    async def start(self) -> None: ...  # 启动 FastAPI 服务

class WebSocketChannelAdapter(ChannelAdapter):
    """WebSocket adapter（通常嵌入 HTTP adapter）"""
    async def handle_ws_connection(ws: WebSocket) -> None: ...

class CLIChannelAdapter(ChannelAdapter):
    """CLI 交互式 adapter"""
    def __init__(self) -> None: ...
    async def start(self) -> None: ...  # 读 stdin，处理用户输入
```

### 决策 4：Gateway 的职责与生命周期

`Gateway` 是长运行进程的核心：

```python
class Gateway:
    """
    1. 管理所有 channel adapters（HTTP、WebSocket、CLI）
    2. 管理 session runners pool
    3. 路由 inbound message 到正确的 session runner
    4. 管理 authorization（token、API key）
    5. 收集指标与日志
    """
    
    def __init__(
        self,
        settings: EduSettings,
        session_store: SessionStore,
        context_manager: ContextManager,
        runtime: ToolRuntime,
    ) -> None: ...
    
    async def start(self) -> None:
        """启动所有 channel adapters，开始处理消息"""
    
    async def process_inbound_message(
        self,
        inbound: InboundMessage,
    ) -> AsyncGenerator[OutboundMessage, None]:
        """
        1. 授权检查（user_id、channel、session 权限）
        2. 获取或创建 session runner
        3. 将消息入队到对应 runner
        4. yield 来自 runner 的 outbound messages
        """
    
    async def stop(self) -> None:
        """优雅关闭所有 channel adapters 与 runners"""
```

### 决策 5：HTTP API 的设计

HTTP API 采用 OpenAI-like 的接口：

```
POST /v1/chat/completions
  请求：{model, messages, stream, temperature, ...}
  响应：{"choices": [...], "usage": {...}}（非流式）
  或：事件流（流式）

POST /v1/sessions
  创建新 session

GET /v1/sessions/{session_id}
  获取 session 详情

GET /v1/sessions
  列表查询

GET /v1/tools
  列表查询可用工具
```

**采用 OpenAI-like 的理由**：

- 前端 / 第三方集成已经了解这个 schema。
- 可直接兼容现有的客户端库。
- 易于与其他 LLM 服务互换。

### 决策 6：SSE Streaming 实现

HTTP `/v1/chat/completions?stream=true` 时，逐条返回事件：

```
data: {"choices": [{"delta": {"content": "你好"}, ...}]}
data: {"choices": [{"delta": {"tool_calls": [...]}, ...}]}
data: [DONE]
```

前端通过 `EventSource` API 接收：

```javascript
const source = new EventSource('/v1/chat/completions?stream=true');
source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    // 更新 UI
};
```

### 决策 7：鉴权与多租户隔离

A5 暂不实现完整的多租户，但框架上预留接口：

```python
class AuthContext(BaseModel):
    user_id: str
    channel: str
    api_key: str | None  # HTTP 请求的 Authorization header
    token: str | None    # JWT（后续 B1 实现）
    permissions: list[str]  # ["read_session", "write_session", ...]

def check_authorization(auth: AuthContext, action: str) -> bool:
    """验证 user_id 和 api_key，检查权限"""
```

### 决策 8：信号处理与优雅关闭

Gateway 收听 SIGTERM、SIGINT 等信号，优雅关闭：

```python
async def shutdown_handler(gateway: Gateway) -> None:
    # 1. 停止接收新消息
    # 2. 等待现有消息处理完成（timeout 30s）
    # 3. 关闭所有 session runners
    # 4. 关闭数据库连接
    # 5. 退出
```

## 文件清单

### 新建文件

- [src/edu_agent/bus/__init__.py](e:/appProjects/eee/src/edu_agent/bus/__init__.py)
  职责：bus 子包导出入口。

- [src/edu_agent/bus/models.py](e:/appProjects/eee/src/edu_agent/bus/models.py)
  职责：定义 `InboundMessage`、`OutboundMessage`、`MessageMetadata`。

- [src/edu_agent/runner/__init__.py](e:/appProjects/eee/src/edu_agent/runner/__init__.py)
  职责：runner 子包导出入口。

- [src/edu_agent/runner/session_runner.py](e:/appProjects/eee/src/edu_agent/runner/session_runner.py)
  职责：`SessionRunner` 类，处理单个 session 的消息。

- [src/edu_agent/runner/gateway.py](e:/appProjects/eee/src/edu_agent/runner/gateway.py)
  职责：`Gateway` 类，统筹所有 channel adapters 与 session runners。

- [src/edu_agent/channels/__init__.py](e:/appProjects/eee/src/edu_agent/channels/__init__.py)
  职责：channels 子包导出入口。

- [src/edu_agent/channels/base.py](e:/appProjects/eee/src/edu_agent/channels/base.py)
  职责：`ChannelAdapter` 抽象基类。

- [src/edu_agent/channels/http.py](e:/appProjects/eee/src/edu_agent/channels/http.py)
  职责：`HTTPChannelAdapter` 与 FastAPI 应用定义。
  提供：
  - `POST /v1/chat/completions` 与 SSE streaming
  - `POST /v1/sessions`
  - `GET /v1/sessions/{session_id}`
  - `GET /v1/sessions`
  - `GET /v1/tools`

- [src/edu_agent/channels/websocket.py](e:/appProjects/eee/src/edu_agent/channels/websocket.py)
  职责：`WebSocketChannelAdapter`（可嵌入 HTTP adapter）。

- [src/edu_agent/channels/cli.py](e:/appProjects/eee/src/edu_agent/channels/cli.py)
  职责：`CLIChannelAdapter`，改造现有 CLI 为 channel adapter。

- [src/edu_agent/api/__init__.py](e:/appProjects/eee/src/edu_agent/api/__init__.py)
  职责：API 子包导出入口。

- [src/edu_agent/api/server.py](e:/appProjects/eee/src/edu_agent/api/server.py)
  职责：`create_app()` 工厂函数，创建 FastAPI 应用；`start_server()` 启动 API 服务。

- [src/edu_agent/auth/__init__.py](e:/appProjects/eee/src/edu_agent/auth/__init__.py)
  职责：auth 子包导出入口。

- [src/edu_agent/auth/models.py](e:/appProjects/eee/src/edu_agent/auth/models.py)
  职责：定义 `AuthContext`、`Credentials`。

- [src/edu_agent/auth/checker.py](e:/appProjects/eee/src/edu_agent/auth/checker.py)
  职责：`AuthorizationChecker` 类，验证 API key、token、权限。

- [src/edu_agent/main.py](e:/appProjects/eee/src/edu_agent/main.py)
  职责：应用入口，解析命令行参数，启动 Gateway 或 CLI channel。

- [tests/edu_agent/test_session_runner.py](e:/appProjects/eee/tests/edu_agent/test_session_runner.py)
  职责：验证 message 串行处理、session 生命周期。

- [tests/edu_agent/test_gateway.py](e:/appProjects/eee/tests/edu_agent/test_gateway.py)
  职责：验证 message routing、authorization、concurrent sessions。

- [tests/edu_agent/test_http_api.py](e:/appProjects/eee/tests/edu_agent/test_http_api.py)
  职责：验证 HTTP 端点、SSE streaming、error handling。

### 修改文件

- [src/edu_agent/cli.py](e:/appProjects/eee/src/edu_agent/cli.py)
  变更：改造为 CLI channel adapter。

- [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py)
  变更：无大改，但 `run_turn()` 返回值改为支持流式数据（用于 StreamingResponse）。

- [README.md](e:/appProjects/eee/README.md)
  变更：补充 HTTP API 使用示例、WebSocket 接入说明、Gateway 启动方式。

- [edu_agent.yaml](e:/appProjects/eee/edu_agent.yaml)（repo root）
  变更：新增 `gateway` 与 `channels` 配置章节。

## 接口契约

### 1. SessionRunner

```python
class SessionRunner:
    def __init__(
        self,
        session_id: str,
        agent_factory,  # 用于创建 Agent instance
        settings: EduSettings,
        context_manager: ContextManager,
        runtime: ToolRuntime,
    ) -> None: ...
    
    async def process_message(
        self,
        inbound: InboundMessage,
    ) -> AsyncGenerator[OutboundMessage, None]: ...
```

### 2. Gateway

```python
class Gateway:
    def __init__(
        self,
        settings: EduSettings,
        session_store: SessionStore,
        context_manager: ContextManager,
        runtime: ToolRuntime,
        auth_checker: AuthorizationChecker,
    ) -> None: ...
    
    async def start(self) -> None: ...
    
    async def process_inbound_message(
        self,
        inbound: InboundMessage,
    ) -> AsyncGenerator[OutboundMessage, None]: ...
    
    async def stop(self) -> None: ...
```

### 3. ChannelAdapter

```python
class ChannelAdapter(ABC):
    @abstractmethod
    async def start(self) -> None: ...
    
    @abstractmethod
    async def stop(self) -> None: ...
    
    async def send_inbound(
        self,
        inbound: InboundMessage,
    ) -> AsyncGenerator[OutboundMessage, None]: ...
```

### 4. HTTP API 例子

```python
@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    stream: bool = False,
) -> Union[ChatCompletionResponse, StreamingResponse]: ...

@app.post("/v1/sessions")
async def create_session(
    request: CreateSessionRequest,
) -> Session: ...

@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str) -> Session: ...

@app.get("/v1/tools")
async def list_tools() -> list[ToolDefinition]: ...
```

## 实施顺序

1. 定义消息模型与 channel adapter 基类。
2. 实现 SessionRunner，验证消息串行处理。
3. 实现 Gateway，支持 message routing。
4. 实现 HTTP channel adapter 与 FastAPI 应用。
5. 改造 CLI 为 channel adapter。
6. 实现 WebSocket channel adapter（可选，与 HTTP 共享）。
7. 实现授权检查与 auth context。
8. 补充测试。
9. 文档与示例。

## 注意事项

### 1. Backpressure 处理

如果客户端接收消息慢，而 Agent 产生消息快，可能导致内存溢出。建议：

- 使用有界 queue（size=100）。
- 若 queue 满，暂停 Agent 执行（backpressure）。

### 2. 错误恢复

若中途某个工具调用失败，Agent 仍应继续运行（不中断整个 session）。确保 ToolRuntime 的错误被正确捕获和转为 OutboundMessage。

### 3. 流式数据的格式

SSE 的每一行应该包含完整的 JSON，避免 parser 错误。建议：

```
data: {"type": "content", "data": "你好"}
data: {"type": "tool_call", "data": {...}}
data: [DONE]
```

### 4. Session 生命周期与资源清理

SessionRunner 长期持有资源（Agent instance、数据库连接）。当 session 长时间无消息时（> idle_timeout），应自动关闭释放资源。

### 5. 性能考虑

初期（A5）不需要考虑高并发（如 10000 并发 sessions），但框架应该可扩展。后期升级为分布式时（多 worker + 消息队列），可直接替换 SessionRunner 的队列为 Redis queue。

## 验收标准

### 消息总线

- 同一 session 的消息严格 FIFO 处理，无乱序。
- 不同 session 的消息并发处理，互不阻塞。

### HTTP API

- `POST /v1/chat/completions?stream=true` 返回 SSE 事件流。
- 非流式模式返回完整 response。
- `GET /v1/sessions` 可列表查询。
- API 正常处理错误（如 invalid session_id、unauthorized），返回 HTTP error code。

### Channel Adapters

- CLI channel 可正常运行，与当前 CLI 体验一致。
- HTTP channel 可通过 FastAPI docs 查看与测试。
- WebSocket channel 可支持多客户端同时连接。

里程碑一致性验收（已确认）：

- 在 A5 阶段验收时，**必须强制执行一次“CLI 走 Gateway 模式”的端到端验证**（即 CLI 作为 channel adapter 接入 Gateway，而非直连 Agent），用于防止“双运行模式行为漂移”。

### 并发

- 10 个并发 session 各发送 10 条消息，全部正确处理。
- 无数据竞争或死锁。

### 优雅关闭

- 发送 SIGTERM 后，等待现有消息处理完成，再关闭。
- 关闭时无错误日志。

## 本阶段不做

- 不做分布式部署（多机器运行 Gateway），那是后续扩展。
- 不做消息持久化到中间件（Redis / RabbitMQ），当前 in-memory queue 足够。
- 不做复杂的速率限制与配额管理。
- 不做完整的多租户隔离（user_id 隔离已足够，细粒度 rbac 留给 B1）。

## 确认的开放点

### 1. CLI 与 Gateway 的关系

方案 A：CLI 作为 channel adapter 接入 Gateway（CI 启动 Gateway，然后连接）。
方案 B：CLI 保持独立，可直接使用 Agent 而不走 Gateway。

> 已确认：同时支持两种模式。默认 CLI 独立运行（快速本地开发），但也支持连接到 Gateway（多 channel 场景）。

### 2. 静态文件与前端

HTTP API 是纯 API，不提供前端 UI。前端由独立的 React 应用提供（后续 B1）。

是否需要在 A5 中提供一个简单的 Web Console（基于 HTML + JS），便于测试 API？

> A5 先不提供 Web Console，专注 API 本身。前端留给 B1+。

### 3. 模型 context 的持久化

当前 Gateway 启动时在内存中维护 active session runners。若进程重启，active sessions 会丢失（但 SessionStore 中的消息仍存）。

是否需要实现"session runner 快照"，支持重启后恢复状态？

> A5 先不做。sessions 在数据库中完整保留，重启后可恢复会话内容。runner 状态丢失只影响"当前执行进度"（通常只有几秒），用户可以重新发送消息。
