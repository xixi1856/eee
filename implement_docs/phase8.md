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

**RAG 契约与演进**（本阶段不重复展开实现细节）：课程 / 个人 **双源**、`knowledge_query` 的 **`sources` 与 `course_id` 边界**（**`course_id` 仅 session runtime，非工具参数**）以 **[Phase 7（B2）](./phase7.md)** 的决策 3、决策 5 与 **「实现边界与工程清单」** 为准。

### 决策 1：聊天交互的数据流

```
Next.js 客户端（Client Component）
  ├─ 学生输入问题 → POST /api/v1/courses/{course_id}/chat（或 EventSource 连到同源 chat-stream）
  │
Next.js 服务端（Route Handler / lib）
  ├─ 验证学生身份与课程权限
  ├─ 创建或获取 Agent session
  ├─ 获取 course_id，作为 context 传递给 Agent
  ├─ 调用 Agent HTTP API（POST /v1/chat/completions?stream=true）
  ├─ 记录 question record 到数据库（可选：流开始前先落库 question 占位）
  │
Agent (Python)
  ├─ 收到消息，创建/加载 session（**runtime** 含 **course_id**，由平台 header 注入，见决策 3）
  ├─ 调用 knowledge_query（如 sources="all"；**不传 course_id 参数**，课程边界仅 runtime，见 Phase 7 决策 5）
  ├─ 生成回答，逐条 yield OutboundMessage（SSE）
  │
Next.js 服务端
  ├─ 接收 Agent 的 SSE 事件流（`fetch` ReadableStream 或 HTTP 客户端）
  ├─ 以 **SSE（text/event-stream）** 转发给浏览器（Route Handler 中 `TransformStream` 等）
  ├─ 流结束后解析回答内容，提取命中的 chunk_id、耗时、token 使用
  ├─ 写入 `qa_logs`（若用户未关闭采集）
  │
Next.js 客户端
  └─ `EventSource` 或 `fetch` + ReadableStream 更新 UI；点击资料链接展开原文
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

当 **Next.js 服务端**调用 Agent HTTP API 时，在 header 中传递：

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
1. **Next.js 定时任务**（如外部 cron 调 `app/api/internal/...`）或独立脚本聚合该学生的最近 `qa_logs`
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

浏览器与 **Next.js Route Handler** 之间的通信方案：

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

### 新建 / 扩展（Next.js + Prisma，`edu-platform/`）

- [edu-platform/prisma/schema.prisma](file:///edu-platform/prisma/schema.prisma)（扩展）
  职责：`QALog`、`LearningProgress`（若采用独立聚合表）等；**Prisma Migrate**。

- [edu-platform/lib/services/chatService.ts](file:///edu-platform/lib/services/chatService.ts)
  职责：调用 Agent `/v1/chat/completions`（stream）、解析 SSE、写 `qa_logs`、尊重用户「关闭采集」标志。

- [edu-platform/lib/services/analyticsService.ts](file:///edu-platform/lib/services/analyticsService.ts)
  职责：教师看板与学生进度的 SQL 聚合（或通过 Prisma `$queryRaw`）。

- [edu-platform/lib/agentClient.ts](file:///edu-platform/lib/agentClient.ts)
  职责：对 Agent 的 HTTP 客户端（base URL、密钥、header 模板）。

- [edu-platform/app/api/v1/courses/[courseId]/chat/route.ts](file:///edu-platform/app/api/v1/courses/[courseId]/chat/route.ts)
  职责：`POST` 聊天并返回 **`text/event-stream`**（代理 Agent SSE）。

- [edu-platform/app/api/v1/courses/[courseId]/chat/history/route.ts](file:///edu-platform/app/api/v1/courses/[courseId]/chat/history/route.ts)
  职责：`GET` 历史（权限见本文接口契约）。

- [edu-platform/app/api/v1/courses/[courseId]/analytics/route.ts](file:///edu-platform/app/api/v1/courses/[courseId]/analytics/route.ts)
  职责：`GET /api/v1/courses/{course_id}/analytics`。

- [edu-platform/app/api/v1/students/[studentId]/learning-progress/route.ts](file:///edu-platform/app/api/v1/students/[studentId]/learning-progress/route.ts)
  职责：`GET` 学生学习进度。

### 新建（Next.js 客户端 UI）

- [edu-platform/components/ChatComponent.tsx](file:///edu-platform/components/ChatComponent.tsx)
  职责：聊天 Client 组件（`"use client"`）：消息气泡、输入框、SSE 消费、历史侧栏。

- [edu-platform/components/MaterialPreview.tsx](file:///edu-platform/components/MaterialPreview.tsx)
  职责：资料预览面板。

- [edu-platform/app/(app)/courses/[courseId]/chat/page.tsx](file:///edu-platform/app/(app)/courses/[courseId]/chat/page.tsx)
  职责：课程聊天页（组合 `ChatComponent` 与 `MaterialPreview`）。

- [edu-platform/app/(app)/courses/[courseId]/analytics/page.tsx](file:///edu-platform/app/(app)/courses/[courseId]/analytics/page.tsx)
  职责：教师数据面板（图表可用 Recharts / ECharts 等）。

- [edu-platform/app/(app)/me/progress/page.tsx](file:///edu-platform/app/(app)/me/progress/page.tsx)
  职责：学生个人学习进度页。

### 新建文件（配置与部署）

- [docker-compose.yml](file:///docker-compose.yml)
  职责：完整系统的 Docker Compose 配置（PostgreSQL、Redis、MinIO、**Next.js** 应用、Python Agent）。

- [docs/deployment.md](file:///docs/deployment.md)
  职责：完整的部署指南（`next build` + Node 生产镜像或 `output: standalone`）。

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

### Next.js（数据模型 + API）

1. 扩展 Prisma schema，**migrate** 建 `qa_logs`（及 `LearningProgress` 若需要）。
2. 实现 `lib/agentClient.ts`、`lib/services/chatService.ts`（流式代理 + 落库）。
3. 实现 `lib/services/analyticsService.ts` 与 **`app/api/v1/**` Route Handlers**。
4. Vitest 单测 + 与 Agent mock 的集成测试。

### Next.js（UI）

1. `ChatComponent`（SSE / fetch stream）与输入区。
2. `MaterialPreview` 与引用高亮。
3. 教师 analytics 页、学生 progress 页。
4. 样式与 UX（含采集告知弹窗）。

### 集成

1. Next.js 与 Agent HTTP API（真实或 staging）联调。
2. 浏览器 ↔ Next.js **同源 SSE** 验证（代理缓冲、断开重连策略）。
3. E2E：学生提问 → Route Handler 调 Agent → 前端流式显示 → `qa_logs` 写入 → 教师看板可读。

## 注意事项

### 1. SSE 的浏览器兼容性

SSE 在现代浏览器中广泛支持，但旧版 IE 不支持。若需兼容，改用 WebSocket 或长轮询。

### 2. 对话长度与上下文

聊天时可能涉及多轮对话。需要明确：

- 每一轮问答是独立记录（`qa_logs`），还是作为一个会话的一部分？
- 多轮对话时，Agent 是否保持上下文（引用前面的问题）？

建议：多轮对话作为同一 `session_id` 的不同 messages，**Next.js 服务端**在调用 Agent 时携带完整 messages 数组（或 Agent 侧已持久化则只传增量，二选一需在实现中固定）。

### 3. 资料引用的准确性

当前方案中，Agent 返回的 `hit_materials` 由 Agent 侧的 `knowledge_query` 决定（**`sources` 与租户边界** 以 Phase 7 为准）。但 Agent 的回答可能引用多个资料但没有显式标注。

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
