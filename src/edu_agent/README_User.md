# EduAgent 使用说明

> 你的 AI 学习助手 — 随时解答问题、生成练习题、记住你的学习进度，并根据你的习惯个性化辅导。

---

## 目录

- [能做什么](#能做什么)
- [安装与准备](#安装与准备)
- [快速开始：命令行对话](#快速开始命令行对话)
- [启动 Web 服务（API 模式）](#启动-web-服务api-模式)
- [功能详解](#功能详解)
  - [知识问答与知识库](#知识问答与知识库)
  - [练习题与作业批改](#练习题与作业批改)
  - [技能（个性化教学方式）](#技能个性化教学方式)
  - [记忆系统](#记忆系统)
  - [网络搜索](#网络搜索)
  - [定时任务](#定时任务)
  - [文件读写](#文件读写)
- [常用命令速查](#常用命令速查)
- [配置文件说明](#配置文件说明)
- [常见问题](#常见问题)
- [获取帮助](#获取帮助)

---

## 能做什么

| 功能 | 说明 |
|------|------|
| 知识问答 | 基于你导入的课程资料，准确回答概念、原理、定义类问题 |
| 练习题生成 | 根据知识库内容，按需生成填空题、选择题、论述题 |
| 作业批改 | 对你的书面作答或代码给出评分和改进建议 |
| 分级提示 | 遇到难题时，给出苏格拉底式引导提示（不直接给答案） |
| 思维导图 | 将知识点整理成可视化思维导图 |
| 记忆学习者 | 自动记录你的掌握情况、偏好和薄弱环节，下次对话时个性化辅导 |
| 网络搜索 | 查询最新资讯、维基百科，补充知识库之外的信息 |
| 定时任务 | 设置定期自动执行的学习提醒或资讯汇总任务 |
| 微信接入 | 支持通过微信聊天直接使用（需额外配置） |

---

## 安装与准备

### 第一步：安装依赖

确保已安装 Python 3.11 或 3.12，然后在项目根目录执行：

```bash
# 推荐使用 uv（速度更快）
pip install uv
uv sync

# 或使用 pip
pip install -e .
```

### 第二步：创建配置文件

将示例配置文件复制为正式配置：

```bash
cp edu_agent.yaml.example edu_agent.yaml
```

用文本编辑器打开 `edu_agent.yaml`，至少填写以下内容：

```yaml
providers:
  dashscope:
    api_key: sk-你的API密钥    # 在阿里云百炼平台获取
```

> **提示**：也可以在项目根目录创建 `.env` 文件，写入 `LLM_API_KEY=sk-你的密钥`，效果相同。

### 第三步：验证安装

```bash
uv run edu --help
```

看到命令列表说明安装成功。

---

## 快速开始：命令行对话

```bash
uv run edu chat
```

启动后，直接输入问题即可：

```
你 > 什么是 TCP 三次握手？
助手 > TCP 三次握手是建立可靠连接的过程，分为三个步骤……
```

**退出对话**：输入 `/quit` 或按 `Ctrl+C`

**重置对话**（清空当前会话历史）：输入 `/reset`

### 切换显示模式

对话过程中，EduAgent 会显示它正在调用哪些工具。在对话中输入 `/verbose` 命令可在以下模式间循环切换：

| 模式 | 说明 |
|------|------|
| `off` | 只显示最终回答，不显示工具调用过程 |
| `new` | 显示工具名称（推荐新用户使用） |
| `all` | 显示工具名称和耗时 |
| `verbose` | 显示完整工具参数和返回摘要（调试用） |

也可以在启动时通过 `--progress` 参数直接指定初始模式：

```bash
uv run edu chat --progress off
```

---

## 启动 Web 服务（API 模式）

如果你想通过网页或其他程序调用 EduAgent，可以启动 HTTP 服务：

```bash
uv run edu-gateway
```

默认地址为 `http://127.0.0.1:8765`，支持以下接口：

| 接口 | 用途 |
|------|------|
| `POST /v1/sessions` | 创建新会话 |
| `POST /v1/sessions/{id}/messages` | 发送消息（支持流式响应） |
| `GET /v1/sessions/{id}` | 查看会话信息 |
| `POST /v1/chat/completions` | OpenAI 兼容接口 |
| `WS /ws/{session_id}` | WebSocket 实时通信 |

---

## 功能详解

### 知识问答与知识库

**导入课程资料**

在对话中直接告诉 EduAgent 导入文档：

```
你 > 请导入这份 PDF：/path/to/计算机网络.pdf
```

或通过命令行工具批量导入（适用于大文件）：

```bash
uv run rag ingest /path/to/文件.pdf
```

支持的格式：PDF、Word、Markdown、TXT 等。

**提问**

导入资料后，直接问问题：

```
你 > 什么是 OSPF 协议？
助手 > 🔍 正在查询知识库...
      OSPF（开放最短路径优先）是一种链路状态路由协议……
```

> 助手会优先从你的课程资料中检索，确保回答准确贴合你的课程内容。

**生成思维导图**

```
你 > 帮我把"运输层"的知识点生成思维导图
助手 > 🗺️ 正在生成思维导图…已保存到 mindmap_storage/
```

---

### 练习题与作业批改

**生成练习题**

```
你 > 根据知识库出5道关于TCP协议的选择题
你 > 我想做些关于操作系统内存管理的练习
```

**提交作答，获得批改**

```
你 > 【题目】什么是进程和线程的区别？
    【我的答案】进程是程序的执行实例，线程是进程中的执行单元，
    线程共享进程的内存空间……
助手 > ✅ 评分：8.5/10
      优点：……
      改进建议：……
```

**提交代码，获得审查**

```
你 > 帮我看看这段代码有没有问题：
    def fibonacci(n):
        if n <= 1: return n
        return fibonacci(n-1) + fibonacci(n-2)
助手 > 💻 代码整体正确，但存在性能问题：没有缓存，
      对 n=40 以上会非常慢，建议使用 @lru_cache 装饰器……
```

**获取分级提示（不想直接看答案时）**

```
你 > 这道题我有点卡住了，给我一个提示就好，不要直接告诉我答案
助手 > 💡 提示：思考一下，"递归"的终止条件是什么？
      如果没有终止条件会发生什么？
```

---

### 技能（个性化教学方式）

EduAgent 内置多种教学策略，可以按你的需求切换风格：

**查看可用教学方式**

```
你 > 有哪些教学方式可以用？
```

**切换教学风格**

```
你 > 用苏格拉底方法给我讲解"递归"
你 > 用脚手架式教学方式帮我学习操作系统
你 > 换一种更直接的方式解释这个概念
```

**创建自定义教学方式**（高级用法）

如果你有特殊的学习偏好，可以请助手创建专属的教学策略文件：

```
你 > 帮我创建一个"费曼技术"教学方式，步骤是：先让我用自己的话解释，
    再指出理解偏差，最后补充完善
```

---

### 记忆系统

EduAgent 会在每次对话结束后自动学习你的情况，下次对话时：
- 记住你已掌握的概念，不再重复讲解
- 知道你的薄弱环节，主动加强
- 了解你的学习偏好（喜欢举例、喜欢推导等）

**手动记录重要信息**

```
你 > 记住：我对图论比较熟悉，不需要从基础开始解释
你 > 记住：我更喜欢通过实际代码例子来理解算法
```

**查看你的学习画像**

```bash
uv run edu show-profile
```

或在对话中：

```
你 > 你对我的学习情况了解多少？
```

**关闭记忆功能**（如临时使用，不想保存记录）

```bash
uv run edu chat --disable-memory
```

---

### 网络搜索

> 需要在配置文件中开启网络权限，或启动时加上 `--allow-network`：

```bash
uv run edu chat --allow-network
```

**搜索最新信息**

```
你 > 搜索一下最新的大模型评测排行榜
你 > 查一下Python 3.13有哪些新特性
```

**查询维基百科**

```
你 > 从维基百科查一下"图灵测试"的定义
```

---

### 定时任务

让 EduAgent 定期自动执行任务（需启动时加上 `--enable-cron`）：

```bash
uv run edu chat --enable-cron --allow-network
```

**创建定时任务**

```
你 > 每天早上9点帮我搜索计算机科学领域的最新动态，
    保存到 output/daily_news.md
```

**查看已有任务**

```
你 > 列出我的所有定时任务
```

**删除任务**

```
你 > 删除定时任务 job_abc123
```

**定时表达式说明**

| 表达式 | 含义 |
|--------|------|
| `every 30m` | 每30分钟 |
| `every 2h` | 每2小时 |
| `every 1d` | 每天 |
| `0 9 * * *` | 每天上午9点 |
| `0 9 * * 1` | 每周一上午9点 |

---

### 文件读写

> 需要启动时加上 `--allow-write` 才能写入文件：

```bash
uv run edu chat --allow-write
```

EduAgent 可以将生成的内容保存到本地文件（仅限 `output/` 目录）：

```
你 > 把刚才生成的题目保存为 output/练习题_TCP.md
你 > 读取 output/练习题_TCP.md
```

---

## 常用命令速查

```bash
# 开始对话（最常用）
uv run edu chat

# 开始对话，使用指定用户名（多用户时区分记忆）
uv run edu chat --user 小明

# 恢复上次的对话
uv run edu chat --session-id <会话ID>

# 开始对话，开启网络搜索
uv run edu chat --allow-network

# 开始对话，开启文件写入
uv run edu chat --allow-write

# 开始对话，跳过工具确认提示
uv run edu chat --approve-all

# 查看你的学习画像
uv run edu show-profile --user 小明

# 查看历史会话列表
uv run edu list-sessions

# 查看当前可用的所有工具
uv run edu list-tools

# 启动 Web 服务（供其他程序调用）
uv run edu-gateway

# 微信登录（扫码绑定）
uv run edu channels login weixin
```

---

## 配置文件说明

配置文件为项目根目录的 `edu_agent.yaml`，常用设置：

```yaml
agent:
  model: qwen-plus-2025-04-28   # 使用的 AI 模型
  temperature: 0.1              # 回答随机性（0=稳定，1=创意）
  max_tokens: 4096              # 单次回答最大长度

providers:
  dashscope:
    api_key: sk-你的密钥         # 必填：AI 服务密钥

tools:
  tavily_api_key: tvly-xxx      # 可选：Tavily 搜索 API（网络搜索更精准）

toolsets:
  memory: true      # 是否启用记忆功能
  rag: true         # 是否启用知识库
  search: true      # 是否启用搜索工具
  files: true       # 是否启用文件读写
  eval: true        # 是否启用评估工具（批改）

runtime:
  gateway:
    host: "127.0.0.1"   # Web 服务监听地址
    port: 8765          # Web 服务端口
```

**关于 API 密钥获取：**
- 阿里云百炼（DashScope）：前往 [https://bailian.console.aliyun.com/](https://bailian.console.aliyun.com/) 注册并创建 API Key
- Tavily 搜索：前往 [https://tavily.com/](https://tavily.com/) 获取（网络搜索可选增强）

---

## 常见问题

**Q：启动时提示"API key 无效"或"认证失败"**

检查 `edu_agent.yaml` 中的 `api_key` 是否正确填写，确认没有多余空格。也可以用 `.env` 文件：
```
LLM_API_KEY=sk-你的密钥
```

---

**Q：问问题时助手说"知识库为空"**

需要先导入课程资料。在对话中输入：
```
你 > 请导入文档 /path/to/你的文件.pdf
```

---

**Q：搜索工具提示"权限不足"**

网络搜索默认关闭（防止意外请求）。启动时加上 `--allow-network`：
```bash
uv run edu chat --allow-network
```

---

**Q：对话记录保存在哪里？**

会话记录保存在工作目录的 `sessions.db` 文件中（SQLite 数据库）。  
记忆文件保存在 `memory/` 目录下，包括：
- `memory/facts/` — 对话中提取的学习事实
- `memory/concepts/` — 聚合的概念掌握度
- `memory/profiles/` — 你的学习者画像

---

**Q：如何在多台设备上同步记录？**

目前数据默认存储在本地。如需同步，可将 `memory/` 和 `sessions.db` 手动备份或通过网盘同步。

---

**Q：对话过程中助手"卡住"很长时间**

这通常是网络请求或知识库检索耗时较长。建议：
1. 检查网络连接
2. 对于大文档检索，等待 5–15 秒属于正常范围
3. 按 `Ctrl+C` 可中断当前对话轮次

---

**Q：如何禁用某个不想用的工具？**

```bash
uv run edu chat --disable-tool web_search --disable-tool cron_job
```

---

**Q：Web 服务地址被占用**

修改 `edu_agent.yaml` 中的端口：
```yaml
runtime:
  gateway:
    port: 9000   # 改为其他端口
```

---

**Q：如何开启微信聊天功能？**

1. 运行登录命令，扫码绑定微信：
   ```bash
   uv run edu channels login weixin
   ```
2. 在 `edu_agent.yaml` 中开启：
   ```yaml
   runtime:
     channels:
       weixin:
         enabled: true
   ```
3. 启动网关服务：
   ```bash
   uv run edu-gateway
   ```

---

## 获取帮助

**查看命令帮助**

```bash
uv run edu --help
uv run edu chat --help
uv run edu-gateway --help
```

**开启调试日志**（排查问题时使用）

```bash
uv run edu --debug chat
```

**查看项目文档**

- 架构与技术文档：[README.md](README.md)
- 实现文档：项目根目录 `implement_docs/` 目录
- API 参考：`docs/api-reference.md`
