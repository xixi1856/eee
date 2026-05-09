# Phase 8：B3 课程聊天界面与学习数据采集详细方案

## 目标与背景

B2 完成后，教育平台已经拥有完整的课程资料管理与 RAG 知识库。B3 是教育平台的最后一阶段，核心目标是建立**学生与 Agent 的聊天交互界面**与**学习行为数据采集系统**，使教育平台能够：

1. **课程页面嵌入聊天**：在课程详情页集成聊天组件，学生可直接提问。
2. **Agent 回答与来源展示**：Agent 的回答流式显示，回答中引用的资料可展开查看原文。
3. **学习数据采集**：记录每次问答的元数据（用户、课程、问题、回答、命中资料、耗时、时间戳）。
4. **教师数据面板**：教师可查看课程维度的问题列表、学生活跃度、薄弱概念。
5. **学习进度追踪**：系统自动汇总学生的学习信息（提问频率、主题分布、困难点）。

B3 完成后，教育平台应该具备以下特征：

- 学生在课程页面发问，Agent 回答实时流式显示。
- 来源资料在回答中可点击展开查看。
- 教师可看到课程级的聚合数据：提问频率、热点问题、学生活跃排行。
- 系统自动识别学生的知识薄弱点，教师可据此调整课程。

## 架构决策

### 决策 1：聊天交互的数据流

```
前端 (React)
  ├─ 学生输入问题 → POST /api/v1/courses/{course_id}/chat
  │
Java 后端
  ├─ 验证学生身份与课程权限
  ├─ 创建或获取 Agent session
  ├─ 获取 course_id，作为 context 传递给 Agent
  ├─ 调用 Agent HTTP API（POST /v1/chat/completions?stream=true）
  ├─ 记录 question record 到数据库
  │
Agent (Python)
  ├─ 收到消息，创建/加载 session（带 course_id context）
  ├─ 调用 knowledge_query（sources="all", course_id）
  ├─ 生成回答，逐条 yield OutboundMessage（SSE）
  │
Java 后端
  ├─ 接收 Agent 的 SSE 事件流
  ├─ 实时转发给前端（WebSocket 或长轮询）
  ├─ 处理完成后，解析回答内容，提取命中的 chunk_id、耗时、token 使用
  ├─ 创建 qa_log record 记录完整交互
  │
前端 (React)
  └─ 实时显示回答文本，点击资料链接可展开原文
```

### 决策 2：问答日志数据模型

```sql
-- QA 日志表
CREATE TABLE qa_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES courses(id),
    student_id UUID NOT NULL REFERENCES users(id),
    lesson_id UUID REFERENCES lessons(id),
    session_id VARCHAR(255) NOT NULL,  -- Agent 侧 session_id
    question TEXT NOT NULL,
    question_tokens INT,
    answer TEXT,
    answer_tokens INT,
    total_tokens INT,
    execution_time_ms INT,  -- Agent 执行耗时
    model_used VARCHAR(100),  -- "gpt-4", "claude-3", ...
    hit_chunks UUID[],  -- 命中的 Material chunk IDs
    hit_materials UUID[],  -- 命中的 Material IDs
    hit_sources TEXT[],  -- ["course", "personal"] 来源标签
    response_quality SMALLINT,  -- 1-5，用户自评（可选）
    is_helpful BOOLEAN,  -- 学生标记"有帮助"（可选）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    agent_feedback TEXT,  -- 如 Agent 有错误或特殊情况的标注
    metadata JSONB  -- 拓展字段
);

-- 日志搜索索引
CREATE INDEX ON qa_logs(course_id, created_at DESC);
CREATE INDEX ON qa_logs(student_id, created_at DESC);
CREATE INDEX ON qa_logs(session_id);
CREATE INDEX ON qa_logs(hit_materials) USING GIN;
```

### 决策 3：Agent 侧的 session context 与权限

当 Java 后端调用 Agent HTTP API 时，在 header 中传递：

```http
POST /v1/chat/completions
X-Platform-User-Id: platform_user_uuid
X-Platform-Session-Id: session_uuid
X-Platform-Course-Id: course_id
X-Platform-Lesson-Id: lesson_id
Content-Type: application/json

{
  "model": "gpt-4",
  "messages": [...],
  "stream": true
}
```

Agent 在 runtime context 中记录这些信息，供工具（如 `knowledge_query`）读取。

### 决策 4：教师数据面板的指标

教师可查看以下维度的数据：

```
课程级聚合：
- 总问答数 (timeline: 每天/每周)
- 平均回答耗时
- 常见问题列表
- 学生活跃排行 (按提问数、最近活动)
- 命中最多的资料
- 回答质量评分分布

学生维度：
- 单个学生的历史问答
- 学生的薄弱概念（通过 Agent memory 系统或问答聚类）
- 学生的学习进度

资料维度：
- 每个资料被引用的次数
- 学生在哪些资料的理解上有困难
```

### 决策 5：学习进度与薄弱点的自动识别

结合 Agent 侧的 memory consolidator 与平台侧的数据分析：

```
每天或每周：
1. Java 后端聚合该学生的最近 qa_logs
2. 调用 Agent 的 analyze_learning_progress API（或直接算法分析）
3. 提取：
   - 提问最多的主题
   - 回答质量最低的主题（薄弱点）
   - 学习速度（新主题覆盖度）
4. 更新 Learning Progress record 或 Learner Profile
5. 教师可在面板中看到这个学生的最新进度
```

### 决策 6：前端聊天组件设计

聊天组件特点：

```
1. 消息气泡：问题（用户）左侧蓝色，回答（Agent）右侧绿色
2. 回答中的资料链接：自动识别 chunk_id，点击可展开原文预览
3. 流式显示：逐字显示 Agent 回答（使用 SSE）
4. 反馈按钮："有帮助"、"无帮助"、"👍 评分"
5. 历史记录：侧边栏显示本会话的问题列表，快速跳转
6. 资料预览面板：展开资料原文时，侧面板显示原文内容与高亮
```

### 决策 7：WebSocket vs 长轮询

前端与 Java 后端之间的通信方案：

**方案 A：WebSocket**
- 优点：双向、低延迟、适合流式数据
- 缺点：连接管理复杂，需要心跳保活

**方案 B：HTTP SSE（Server-Sent Events）
- 优点：单向流式、简单、无需 upgrade
- 缺点：不支持双向

**推荐**：HTTP SSE（仅需单向流）。

前端建立 EventSource 连接：

```javascript
const eventSource = new EventSource(
  `/api/v1/courses/${courseId}/chat-stream?session_id=${sessionId}`
);
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // 显示 Agent 的回复文本、资料链接等
  updateChatUI(data);
};
eventSource.onerror = () => {
  eventSource.close();
};
```

### 决策 8：数据隐私与用户同意

平台采集学生的提问内容与行为数据。建议：

- 在首次使用聊天功能时显示"数据采集声明"。
- 学生可选择"不收集我的数据"（此时仍可用聊天，但不记录到数据库）。
- 教师与学生都可导出自己的数据。
- 提供数据删除接口（符合 GDPR 等隐私规范）。

默认策略（已确认）：

- **默认开启采集**：首次进入课程聊天功能时展示告知弹窗（或页面 Banner），明确“采集范围/用途/保存期限/可见性”。
- **允许关闭**：学生可在该告知处或个人设置中关闭采集。关闭后聊天仍可用，但不写入 `qa_logs`（仍允许生成会话内回复）。

## 文件清单

### 新建文件（Java 后端）

- [edu-platform/src/main/java/com/eduagent/entity/QALog.java](file:///edu-platform/src/main/java/com/eduagent/entity/QALog.java)
  职责：JPA QALog 实体。

- [edu-platform/src/main/java/com/eduagent/entity/LearningProgress.java](file:///edu-platform/src/main/java/com/eduagent/entity/LearningProgress.java)
  职责：JPA LearningProgress 实体（聚合数据）。

- [edu-platform/src/main/java/com/eduagent/repository/QALogRepository.java](file:///edu-platform/src/main/java/com/eduagent/repository/QALogRepository.java)
  职责：QALog 数据访问接口。

- [edu-platform/src/main/java/com/eduagent/dto/ChatRequest.java](file:///edu-platform/src/main/java/com/eduagent/dto/ChatRequest.java)
  职责：聊天请求 DTO。

- [edu-platform/src/main/java/com/eduagent/dto/ChatStreamEvent.java](file:///edu-platform/src/main/java/com/eduagent/dto/ChatStreamEvent.java)
  职责：聊天流事件 DTO（SSE 格式）。

- [edu-platform/src/main/java/com/eduagent/dto/AnalyticsResponse.java](file:///edu-platform/src/main/java/com/eduagent/dto/AnalyticsResponse.java)
  职责：教师数据面板的响应 DTO。

- [edu-platform/src/main/java/com/eduagent/service/ChatService.java](file:///edu-platform/src/main/java/com/eduagent/service/ChatService.java)
  职责：聊天业务逻辑，包括调用 Agent API、记录日志。

- [edu-platform/src/main/java/com/eduagent/service/AnalyticsService.java](file:///edu-platform/src/main/java/com/eduagent/service/AnalyticsService.java)
  职责：数据分析业务逻辑（聚合、薄弱点识别）。

- [edu-platform/src/main/java/com/eduagent/service/AgentApiClient.java](file:///edu-platform/src/main/java/com/eduagent/service/AgentApiClient.java)
  职责：HTTP client，调用 Agent /v1/chat/completions。

- [edu-platform/src/main/java/com/eduagent/controller/ChatController.java](file:///edu-platform/src/main/java/com/eduagent/controller/ChatController.java)
  职责：聊天端点：
  - `POST /api/v1/courses/{course_id}/chat` — 发送问题（返回 SSE stream）
  - `GET /api/v1/courses/{course_id}/chat/history` — 问答历史

- [edu-platform/src/main/java/com/eduagent/controller/AnalyticsController.java](file:///edu-platform/src/main/java/com/eduagent/controller/AnalyticsController.java)
  职责：数据分析端点：
  - `GET /api/v1/courses/{course_id}/analytics` — 课程聚合数据
  - `GET /api/v1/students/{student_id}/learning-progress` — 学生进度

- [edu-platform/src/main/resources/db/migration/V3__qa_logs_analytics.sql](file:///edu-platform/src/main/resources/db/migration/V3__qa_logs_analytics.sql)
  职责：Flyway 迁移脚本，建 QALog 和 LearningProgress 表。

### 新建文件（React 前端）

- [edu-platform-web/src/components/ChatComponent.tsx](file:///edu-platform-web/src/components/ChatComponent.tsx)
  职责：聊天组件，包含消息气泡、输入框、历史记录侧边栏。

- [edu-platform-web/src/components/MaterialPreview.tsx](file:///edu-platform-web/src/components/MaterialPreview.tsx)
  职责：资料预览面板。

- [edu-platform-web/src/pages/CourseChat/index.tsx](file:///edu-platform-web/src/pages/CourseChat/index.tsx)
  职责：课程聊天页面（集成聊天组件与资料预览）。

- [edu-platform-web/src/pages/CourseAnalytics/index.tsx](file:///edu-platform-web/src/pages/CourseAnalytics/index.tsx)
  职责：教师数据面板页面，展示聚合数据与图表。

- [edu-platform-web/src/services/chatService.ts](file:///edu-platform-web/src/services/chatService.ts)
  职责：聊天 API 调用、EventSource 管理。

- [edu-platform-web/src/services/analyticsService.ts](file:///edu-platform-web/src/services/analyticsService.ts)
  职责：数据分析 API 调用。

- [edu-platform-web/src/pages/StudentProgress/index.tsx](file:///edu-platform-web/src/pages/StudentProgress/index.tsx)
  职责：学生个人学习进度页面。

### 新建文件（配置与部署）

- [docker-compose.yml](file:///docker-compose.yml)
  职责：完整系统的 Docker Compose 配置（PostgreSQL、Redis、MinIO、Java 后端、Python Agent、前端）。

- [docs/deployment.md](file:///docs/deployment.md)
  职责：完整的部署指南。

## 接口契约

### 1. POST /api/v1/courses/{course_id}/chat (发送问题，SSE 流)

**请求**：
```json
{
  "message": "如何理解 Python 的装饰器？",
  "lesson_id": "lesson_uuid" (可选)
}
```

**响应**（HTTP 200，Content-Type: text/event-stream）：
```
data: {"type": "text", "content": "装"}
data: {"type": "text", "content": "饰"}
data: {"type": "text", "content": "器"}
data: {"type": "citation", "chunk_id": "chunk_xxx", "material_id": "mat_yyy"}
data: {"type": "done", "tokens": 150, "exec_time_ms": 2500}
```

### 2. GET /api/v1/courses/{course_id}/chat/history

**查询参数**：
- `limit`: 20
- `offset`: 0

权限约束：

- 学生：仅可查询自己的历史记录。
- 教师：默认仅可查询课程级聚合，不返回单个学生逐条问答明细。
- 管理员：可在审计场景下按 `student_id` 过滤查看明细（需显式审计权限）。

**响应**：
```json
{
  "logs": [
    {
      "id": "qa_log_uuid",
      "question": "...",
      "answer": "...",
      "created_at": "...",
      "hit_materials": ["mat_id_1", "mat_id_2"]
    }
  ],
  "total": 100
}
```

### 3. GET /api/v1/courses/{course_id}/analytics

**查询参数**：
- `start_date`: 2026-05-01
- `end_date`: 2026-05-08

**响应**：
```json
{
  "total_questions": 250,
  "avg_response_time_ms": 3500,
  "top_questions": [
    {
      "question": "...",
      "count": 15,
      "avg_quality": 4.2
    }
  ],
  "active_students": [
    {
      "student_id": "...",
      "name": "...",
      "question_count": 20,
      "last_active": "..."
    }
  ],
  "top_materials": [
    {
      "material_id": "...",
      "title": "...",
      "hit_count": 30
    }
  ],
  "weak_concepts": [
    {
      "concept": "...",
      "count": 10,
      "resources": ["mat_id_1", "mat_id_2"]
    }
  ]
}
```

### 4. GET /api/v1/students/{student_id}/learning-progress

**响应**：
```json
{
  "student_id": "...",
  "total_questions": 50,
  "topics_covered": ["decorators", "async", "..."],
  "weak_areas": ["async-io", "..."],
  "recent_activity": "2026-05-08 15:30",
  "engagement_score": 0.85
}
```

## 实施顺序

### 后端（Java）

1. 新增数据库迁移脚本。
2. 实现 JPA 实体与 Repository。
3. 实现 ChatService（调用 Agent API、记录日志）。
4. 实现 AnalyticsService（数据聚合分析）。
5. 实现 ChatController 与 AnalyticsController。
6. 测试与集成。

### 前端（React）

1. 聊天组件与输入框。
2. SSE 流接收与实时显示。
3. 资料预览面板。
4. 教师数据面板（图表、表格）。
5. 学生进度页面。
6. 样式与 UX。

### 集成

1. Java 后端与 Agent HTTP API 的通信。
2. 前端与 Java 后端的 SSE 通信。
3. E2E：学生提问 → Java 后端调用 Agent → 前端实时显示 → 数据记录 → 教师查看面板。

## 注意事项

### 1. SSE 的浏览器兼容性

SSE 在现代浏览器中广泛支持，但旧版 IE 不支持。若需兼容，改用 WebSocket 或长轮询。

### 2. 对话长度与上下文

聊天时可能涉及多轮对话。需要明确：

- 每一轮问答是独立记录（`qa_logs`），还是作为一个会话的一部分？
- 多轮对话时，Agent 是否保持上下文（引用前面的问题）？

建议：多轮对话作为同一 `session_id` 的不同 messages，Java 后端将整个会话传给 Agent。

### 3. 资料引用的准确性

当前方案中，Agent 返回的 `hit_materials` 由 Agent 侧的 `knowledge_query` 决定。但 Agent 的回答可能引用多个资料但没有显式标注。

改进方案（后续）：Agent 在生成回答时同时返回 `citations`（引文信息），格式如 `[1] ... [2] ... with reference [1][2]`。

### 4. 数据容量与性能

随着时间推移，`qa_logs` 表会快速增长。建议：

- 按年/月分区（PostgreSQL partitioning）。
- 对旧数据（> 1 年）进行归档或删除。
- 聚合查询时限制时间范围（如仅查最近 30 天）。

### 5. 学生数据隐私

采集学生的提问内容，涉及隐私问题。建议：

- 明确告知学生"您的提问会被记录用于教学分析"。
- 提供"匿名模式"（提问不记录学生身份）。
- 允许学生导出或删除自己的数据。

## 验收标准

### 聊天交互

- 学生在课程页面提问，前端实时显示 Agent 回答（流式）。
- 回答中包含资料链接，点击可展开原文预览。
- 系统正确识别并显示来源（课程 RAG 或个人 RAG）。

### 数据采集

- 每次问答的元数据（问题、回答、命中资料、耗时）完整记录。
- QALog 表中有对应的记录。

### 教师面板

- 教师可看到课程的聚合数据（总问答数、活跃学生排行、热点问题）。
- 可选时间范围查看（如"最近一周"）。
- 数据准确度验证（手工抽查）。

### 学生进度

- 学生可看到自己的学习进度（提问数、薄弱点）。
- 系统自动识别薄弱概念并推荐资料。

## 本阶段不做

- 不做作业发布与自动批改。
- 不做直播与实时转录。
- 不做复杂的 BI 看板与 AI 洞察。
- 不做学生与学生之间的 QA 论坛。

## 确认的开放点

### 1. 聊天历史的范围

- 是否每个学生与每个课程单独维护聊天历史？
- 还是跨课程共享（同一学生的历史消息在所有课程中可见）？

> 按课程隔离（每个课程单独的聊天界面）。跨课程共享会导致混乱。

### 2. 教师的权限

- 教师是否可以查看学生的具体提问内容？
- 还是仅能看到聚合统计（"10 个学生问过关于装饰器的问题"）？

> 已确认：教师只看聚合数据，不看单个学生完整提问明细；超级管理员可在审计权限下查看明细。

### 3. 回答质量评分

- 前端是否显示学生的评分（"这个回答有帮助"）？
- 教师是否基于这个评分调整 Agent？

> 显示评分但暂不用于调整 Agent。后期可基于评分数据优化 prompt 或更换模型。
