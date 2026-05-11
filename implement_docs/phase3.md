# Phase 3：A3 长期记忆与学习者画像自动更新详细方案

## 目标与背景

A2 完成后，EduAgent 已经拥有完整的会话存储与上下文管理。A3 的核心目标是建立**学习者记忆系统**，使 Agent 能够：

1. **跨会话持久化学习者的知识、偏好、薄弱点**：单靠会话历史只能记住当前对话，跨会话遗忘。
2. **自动化学习者画像更新**：每次会话结束时，自动提取事实、偏好、进度，更新画像，无需用户手动操作。
3. **记忆检索与引用**：Agent 在新会话中可主动回忆并引用记忆，增强对话的连贯性和个性化。
4. **记忆协整与冲突解决**：当新事实与旧记忆冲突时（如"学过二次方程"vs"不会解二次方程"），能自动协整。

A3 完成后，EduAgent 应该具备以下特征：

- 学习者画像不只是静态 JSON，而是**动态、可自动演进的知识库**。
- Agent 与学习者的对话越多，Agent 对学习者的理解越深。
- Agent 可以利用记忆主动提供个性化建议（"你上次在分数化简这块有困难，今天想继续?"）。

**A3 当前实现范围（与后续能力区分）**：记忆按 **user_id** 维度落盘与检索；**课程 / 跨课程过滤、课程级物理隔离** 不在本阶段实现（与「决策 8」中检索过滤的完整形态相比延后至 B2+ 或专项迭代）。产品文案中若提到「课程内优先」等，以本节为准：**当前版本为全局（每用户）记忆池**。

## 架构决策

### 决策 1：记忆分层存储模型

记忆分为三层，按粒度从细到粗：

```
Fact Layer (事实层) — 原始观察
  Example: "用户在 2026-05-08 09:30 解决了问题 'solve_quadratic'"
           "用户在提问时说'我不喜欢背公式'"

Concept Layer (概念层) — 聚合后的理解
  Example: "用户掌握了一元二次方程求解 (confidence: 0.8)"
           "用户偏好推导式学习而非死记"

Profile Layer (画像层) — 结构化学习者画像
  Example: strength: [concept_id_1, concept_id_2, ...]
           weakness: [concept_id_3, concept_id_4, ...]
           preferences: {learning_style: "推导式", pace: "快"}
```

**不采用单层扁平记忆**的原因：

- 单层无法体现信息从具体到抽象的演进。
- 无法追溯某个结论的来源（为什么认为这个学生掌握了某个概念？）。
- 冲突检测与协整困难。

### 决策 2：记忆存储采用本地 JSON + 图式演化

初期（A3）存储方案：

- **Fact**：写入 `{workspace}/memory/facts/{user_id}/{date}.jsonl`，append-only，每行一条事实。
- **Concept**：写入 `{workspace}/memory/concepts/{user_id}.json`，按概念组织，支持覆盖。
- **Profile**：写入 `{workspace}/memory/profiles/{user_id}.json`，定期覆盖更新。

**不采用 Graph DB 的原因**：

- 项目当前还是单机 Agent，图谱查询需求不迫切。
- JSON 足够支撑三层结构。

**升级路径**（预留给 A4+）：

- Fact 可升级为 PostgreSQL table，便于全文搜索与时间范围查询。
- Concept graph 可升级为 Neo4j，支持路径查询与知识融合。

### 决策 3：记忆提取策略 — 会话级 Consolidator

在以下时机触发 **memory consolidator**（由 CLI / 编排层调用，**不在每一轮 `run_turn()` 末尾无条件执行**，以避免 LLM 成本与重复提取）：

1. **交互会话结束**：CLI `chat` 在用户 `/quit`、`/exit` 或 EOF 退出时，在 `finally` 中调用一次（加载当前 `session_id` 的完整消息列表后执行）。
2. **Token 阈值**：当本会话累计消息粗估 token ≥ 配置阈值（如 `MemoryConfig.extraction_min_session_tokens`）时，可在 `run_turn()` 末尾做**轻量判断**，满足条件则触发一次提取与下游聚合（具体「每会话最多触发几次」由实现约定，建议至少保证会话结束仍会再跑一遍以覆盖尾部消息）。

```
触发（结束 | 阈值 ）
  ↓
1. 从 SessionStore 读取 session 中 messages（user + assistant + tool 等）
2. 调用 LLM：从对话中提取事实与观察（structured extraction）
3. 写入 Fact layer（append-only）
4. 周期性或每次 consolidator 末尾：聚合最近 Facts 生成 Concepts
5. 聚合 Concepts 更新 Profile
```

### 决策 4：记忆与 Fact 的数据模型

Fact（事实）定义为：

```python
class Fact(BaseModel):
    id: str  # uuid
    user_id: str
    session_id: str
    timestamp: datetime
    category: Literal[
        "concept_mastery",      # 掌握了某概念
        "concept_confusion",    # 混淆了某概念
        "preference",           # 偏好表达
        "difficulty",           # 困难点记录
        "question",             # 提过的问题
        "achievement",          # 成就记录
    ]
    content: str  # 自然语言描述或结构化 JSON
    confidence: float  # 0.0 - 1.0，该事实的可信度
    source: dict  # 来源信息：{message_id, tool_call, ...}
    metadata: dict  # 拓展字段
```

Concept（概念）定义为：

```python
class Concept(BaseModel):
    id: str  # 概念唯一 ID（如 "math.quadratic_eq.solving"）
    name: str
    description: str
    mastery_level: float  # 0.0 - 1.0
    last_updated: datetime
    facts: list[str]  # 支撑这个概念判断的 fact IDs
    related_concepts: list[str]  # 关联概念 ID
    metadata: dict
```

### 决策 5：自动冲突检测与协整规则

当新 Fact 与现有 Profile 冲突时，采用以下规则：

1. **Recency 优先**：新事实比旧事实权重更高。
2. **Confidence 权重**：高 confidence 的事实覆盖低 confidence 的。
3. **多源认证**：同一结论从多个独立 session/工具得出时，confidence 提升。
4. **显式否定**：如果新事实是"用户纠正了之前的错误认知"，则旧事实标记为 `deprecated`。

### 决策 6：记忆检索与过滤

Agent 在新会话中可通过以下接口查询记忆：

```python
# 精确查询
remember_concept(user_id, concept_id) -> Concept | None

# 模糊检索
search_concepts(user_id, keyword) -> list[Concept]

# 当前会话相关的记忆（实现类名为 MemoryRetriever.get_relevant_concepts）
get_relevant_concepts(user_id, session_context) -> list[Concept]
```

其中 `session_context` 为 dict（如 `topic`、`keywords` 等），用于与概念库文本做 **关键词匹配 + TF-IDF 打分** 的相关性排序。**不**在 A3 使用 embedding / ANN；文档中若出现「语义检索」字样，在本阶段指 **非向量的统计相关性**（与下文「本阶段不做」一致）。向量检索接口可预留至 A4。

### 决策 7：学习者画像 Profile 的结构

```python
class LearnerProfile(BaseModel):
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    # 基础信息
    name: str
    learning_goal: str | None
    
    # 学习进度（按课程 / 全局）
    concepts_mastered: list[Concept]  # 掌握的概念
    concepts_struggling: list[Concept]  # 困难概念
    recent_topics: list[str]  # 最近讨论的主题
    
    # 学习风格与偏好
    learning_style: Literal["推导式", "应用式", "混合"] | None
    pace_preference: Literal["快", "中", "慢"] | None
    interaction_frequency: dict  # 学习频率统计
    
    # 总体评分
    overall_engagement: float  # 0.0 - 1.0
    progress_trend: Literal["上升", "平稳", "下降"]
    
    # 历史快照（用于趋势分析）
    snapshots: list[{timestamp, concepts_mastered_count, ...}]
```

### 决策 8：不在 A3 实现多租户记忆隔离

已确认的作用域策略：

- 存储层在 A3 按 **user_id** 维度落盘（便于实现与迁移）。
- **课程维度、跨课程检索过滤**（见上文「目标与背景」曾描述的理想行为）**不在 A3 实现**；检索在单用户概念池内进行。多租户权限、课程级物理隔离留到 B2+ 或专项迭代。

## 文件清单

### 新建文件

- [src/edu_agent/memory/__init__.py](e:/appProjects/eee/src/edu_agent/memory/__init__.py)
  职责：memory 子包导出入口。

- [src/edu_agent/memory/models.py](e:/appProjects/eee/src/edu_agent/memory/models.py)
  职责：定义 `Fact`、`Concept`、`LearnerProfile`、`MemoryConfig` 等数据模型。

- [src/edu_agent/memory/storage.py](e:/appProjects/eee/src/edu_agent/memory/storage.py)
  职责：`MemoryStore` 类，提供：
  - `add_fact(fact: Fact) -> None`
  - `get_facts(user_id, date_range, category) -> list[Fact]`
  - `search_facts(user_id, keyword) -> list[Fact]`
  - `save_concept(user_id, concept: Concept) -> None`
  - `get_concept(user_id, concept_id) -> Concept | None`
  - `search_concepts(user_id, keyword) -> list[Concept]`
  - `load_profile(user_id) -> LearnerProfile | None`
  - `save_profile(profile: LearnerProfile) -> None`

- [src/edu_agent/memory/extractor.py](e:/appProjects/eee/src/edu_agent/memory/extractor.py)
  职责：`MemoryExtractor` 类，提供：
  - `extract_facts_from_session(session_id, messages) -> list[Fact]`
    调用 LLM 的 structured extraction 模式，返回从对话中提取的事实列表。
  - 实现具体的 extraction prompt 与响应解析。

- [src/edu_agent/memory/consolidator.py](e:/appProjects/eee/src/edu_agent/memory/consolidator.py)
  职责：`MemoryConsolidator` 类，提供：
  - `consolidate_session(user_id, session_id, messages) -> None`
    主入口：由编排层传入已从 SessionStore 加载的 `messages`，触发 extraction → storage → aggregation（编排层负责「何时调用」，见决策 3）。
  - `aggregate_facts_to_concepts(user_id) -> list[Concept]`
    周期性聚合最近的 facts 生成 concepts。
  - `aggregate_concepts_to_profile(user_id) -> LearnerProfile`
    周期性从 concepts 更新 profile。
  - `detect_and_resolve_conflicts(user_id, new_fact) -> None`
    检测新事实与现有 profile 的冲突，应用协整规则。

- [src/edu_agent/memory/retriever.py](e:/appProjects/eee/src/edu_agent/memory/retriever.py)
  职责：`MemoryRetriever` 类，提供：
  - `get_relevant_concepts(user_id, session_context) -> list[Concept]`
    根据 `session_context`（`topic`、`keywords` 等）在概念库上做 **关键词匹配 + TF-IDF** 相关排序（A3 不向量化）。
  - `search_concepts(user_id, keyword) -> list[Concept]`
  - 预留接口供 A4+ 接入向量数据库；A3 不实现 ANN。

- [src/edu_agent/tools/memory.py](e:/appProjects/eee/src/edu_agent/tools/memory.py)
  职责：定义 Agent 可调用的记忆相关工具：
  - `remember_fact(fact_content: str) -> str`：Agent 可主动记录某个事实。
  - `search_memory(keyword: str) -> list[str]`：Agent 查询相关记忆。
  - `update_profile_note(note: str) -> str`：Agent 可主动补充画像信息；实现上写入 `LearnerProfile` 中约定的结构化字段（如带时间戳的 `assistant_notes` 列表），由 `MemoryStore.save_profile` 持久化，与 Fact/Consolidator 协整路径并存（便于审计与展示）。

- [tests/edu_agent/test_memory_store.py](e:/appProjects/eee/tests/edu_agent/test_memory_store.py)
  职责：验证 facts/concepts/profile 的 CRUD、搜索、持久化。

- [tests/edu_agent/test_memory_extractor.py](e:/appProjects/eee/tests/edu_agent/test_memory_extractor.py)
  职责：验证从 session 消息提取事实的准确性，mocking LLM 调用。

- [tests/edu_agent/test_memory_consolidator.py](e:/appProjects/eee/tests/edu_agent/test_memory_consolidator.py)
  职责：验证会话级和周期级的记忆聚合与冲突协整。

### 修改文件

- [src/edu_agent/agent.py](e:/appProjects/eee/src/edu_agent/agent.py)
  变更：
  - 构造函数可选注入 `memory_consolidator: MemoryConsolidator | None`（或由 Agent 内部在 `memory_enabled` 时构造默认实例）。
  - **`run_turn()` 不在每轮末尾无条件调用** `consolidate_session`；仅在满足 **Token 阈值** 时可选触发（见决策 3）；**会话结束**编排调用 consolidator。
  - 提供记忆检索供工具使用；可选在 `build_system_prompt` 中通过显式参数注入「相关记忆」文本块（需配置开启，避免隐藏注入）。

- [src/edu_agent/types.py](e:/appProjects/eee/src/edu_agent/types.py)
  变更：`AgentConfig` 新增 `memory_enabled: bool` 字段（允许关闭记忆以节省成本或测试）。

- [src/edu_agent/learner_profile.py](e:/appProjects/eee/src/edu_agent/learner_profile.py)
  变更：
  - 重新定义为"从 memory module 加载 profile"而非独立模块。
  - 提供读接口 `load_learner_profile(user_id)` 和写接口通过 MemoryConsolidator 完成。
  - 或改为 wrapper，调用 MemoryStore 的 profile 接口。

- [src/edu_agent/cli.py](e:/appProjects/eee/src/edu_agent/cli.py)
  变更：
  - 新增 `show-profile` 子命令，展示当前用户（或指定用户）的学习画像。
  - 新增 `--disable-memory` 标志，启动时关闭记忆系统。
  - 在退出 `finally` 中调用 consolidator。

- [README.md](e:/appProjects/eee/README.md)
  变更：补充记忆系统的说明、如何查看学习画像、如何禁用记忆。

## 接口契约

### 1. MemoryExtractor

```python
class MemoryExtractor:
    def __init__(
        self,
        runtime: ResolvedProviderRuntime,
        settings: EduSettings,
    ) -> None: ...
    
    def extract_facts_from_session(
        self,
        user_id: str,
        session_id: str,
        messages: list[Message],
    ) -> list[Fact]: ...
```

### 2. MemoryConsolidator

```python
class MemoryConsolidator:
    def __init__(
        self,
        store: MemoryStore,
        extractor: MemoryExtractor,
        settings: EduSettings,
    ) -> None: ...
    
    def consolidate_session(
        self,
        user_id: str,
        session_id: str,
        messages: list[Message],
    ) -> None: ...
    
    def aggregate_facts_to_concepts(
        self,
        user_id: str,
        days_lookback: int = 7,
    ) -> list[Concept]: ...
    
    def aggregate_concepts_to_profile(
        self,
        user_id: str,
    ) -> LearnerProfile: ...
```

### 3. MemoryRetriever

```python
class MemoryRetriever:
    def __init__(self, store: MemoryStore) -> None: ...
    
    def get_relevant_concepts(
        self,
        user_id: str,
        session_context: dict,  # {topic, keywords, ...}
    ) -> list[Concept]: ...
    
    def search_concepts(
        self,
        user_id: str,
        keyword: str,
    ) -> list[Concept]: ...
```

## 实施顺序

1. 定义数据模型，创建存储目录与序列化。
2. 实现 MemoryStore 的 CRUD。
3. 实现 MemoryExtractor。
4. 实现 MemoryConsolidator 的聚合与冲突协整逻辑。
5. 实现 MemoryRetriever 的检索。
6. 集成到 CLI（会话结束、可选 token 阈值经 Agent 轻量触发）与 Agent（`memory_enabled`、工具、可选 prompt 记忆块）。
7. 补充测试与 CLI 命令。

## 注意事项

### 1. Memory Extraction 的 LLM 成本

每次会话结束都要调用 LLM 提取事实，这增加 API 开销。建议：

- 仅在会话 token 数达到某个阈值（如 > 1000 tokens）时才提取。
- 提供配置 `extraction_enabled: bool`。
- 后续 A4 可优化为"只在关键 tool_call 时提取"。

### 2. 数据隐私

如果项目后期涉及真实教学数据，应考虑：

- 加密存储 facts（包含用户对话内容）。
- 提供数据导出与删除接口。

A3 暂不实现，标记为未来要点。

### 3. 概念库的管理

系统自动从 facts 生成 concepts，但概念库的"规范化"需要人工维护。例如：

- "解一元二次方程"与"求解二次方程"应该合并为同一概念。
- 新概念的定义与名词应该标准化。

A3 不处理这个，假设概念库由教师/管理员维护。

### 4. Profile 的版本化

Profile 每次更新前应保存快照（snapshot），用于后续的学习趋势分析。当前代码中已预留 `snapshots` 字段。

### 5. 跨课程记忆隔离

> **A3 当前不实现**：课程级过滤与跨课程引用规则延后；待引入 `course_id` 或等价维度并更新检索层后再启用原文所述策略。

## 验收标准

### 记忆提取

- 会话结束后，自动从对话中提取出 3~10 条 facts，写入 storage。
- 每条 fact 都附带 confidence 和 source 信息。

### 概念聚合

- 定期（或手工触发）将过去 7 天的 facts 聚合成 concepts。
- 同一概念的多条事实合并为单条 concept record，confidence 值累积更新。

### 画像更新

- 学习者画像定期从 concepts 更新，包含 mastered、struggling、preferences 等字段。
- 可通过 `edu show-profile` 查看当前用户的画像。

### 冲突解决

- 新事实与现有画像有矛盾时，根据 confidence、recency 规则自动协整，不报错。
- 测试中验证"旧概念被否定后被标记为 deprecated"。

### 记忆检索

- Agent 可通过 `search_memory` 工具查询相关记忆。
- 检索结果按相关度排序。

## 本阶段不做

- 不做向量化 embedding 与语义搜索（ANN），使用简单关键词匹配。后续 A4 可升级。
- 不做记忆的多租户隔离或权限控制。
- 不做 facts 的全文搜索索引，JSON 扫描足够。
- 不做 fact 的加密存储。

## 与 Hermes Memory 分层对照（延伸阅读）

NousResearch **hermes-agent** 将 `MemoryManager` / `MemoryProvider` 与上下文压缩分层；EduAgent A3 在编排上与之对齐的说明与差距矩阵见仓库内 **[review_docs/hermes_memory_gap.md](../review_docs/hermes_memory_gap.md)**（含 P0/P1/P2 优先级与刻意不对标项）。

## 确认的开放点

### 1. 记忆提取的 prompt 复杂度

当前方案是："给定会话的消息列表，提取结构化 facts"。

是否需要提供多个 extraction prompt，根据不同教学场景（数学、语言、编程）定制化提取？

> A3 先用通用 prompt（识别所有教学场景的通用 fact 类型），后续按需定制。

### 2. 概念 ID 的标准化

当前方案中概念 ID 如 "math.quadratic_eq.solving" 需要手工定义或从某个知识本体导入。

是否需要在 A3 中提供"自动生成概念 ID"的机制，或依赖教师手工维护概念库？

> A3 允许系统自动生成无规范化的 concept_id（如 hash(concept_name)），后期 B2 再引入标准概念库。
