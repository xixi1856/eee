# RAG MVP

基于 **RAG-Anything** + **MinerU** + **Qwen API** 构建的多模态 RAG 命令行工具。

支持格式：PDF、DOCX、PPTX、XLSX、TXT、MD、JPG、PNG  
（DOCX / PPTX 需要安装 LibreOffice）

---

## 环境要求

| 工具 | 版本 |
|------|------|
| Python | 3.11 |
| uv | 最新 |
| LibreOffice（可选） | 7.x+ |

---

## 安装

### 1. 安装 uv

```powershell
pip install uv
```

### 2. 创建虚拟环境并安装依赖

```powershell
uv sync
```

### 3. 配置环境变量

```powershell
copy .env.example .env
```

编辑 `.env`，填入你的 DashScope API Key：

```
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
```

### 4. 初始化 MinerU 模型（首次运行，约 2-4 GB）

MinerU 会在首次解析文档时自动从 ModelScope 下载模型，无需手动操作。  
如需提前下载：

```powershell
uv run mineru-models-download -s modelscope -m pipeline
```

### 5. 安装 LibreOffice（处理 DOCX / PPTX）

从 [https://www.libreoffice.org/download/](https://www.libreoffice.org/download/) 下载安装 Windows 版本，  
安装后重启终端，确保 `soffice` 命令可访问（或将安装目录加入 PATH）。

---

## 使用

### 检查状态

```powershell
uv run rag status
```

### 入库文档（单文件）

```powershell
uv run rag ingest data\input\report.pdf
uv run rag ingest data\input\slides.pptx
uv run rag ingest data\input\photo.png
```

### 入库文档（整个文件夹）

```powershell
uv run rag ingest data\input\
```

### 查询

```powershell
uv run rag query "文档中的主要结论是什么？"

# 指定检索模式
uv run rag query "总结所有表格数据" --mode local
uv run rag query "整体主题是什么？" --mode global
uv run rag query "第一章讲了什么？" --mode naive
```

#### 检索模式说明

| 模式 | 适用场景 |
|------|---------|
| `hybrid`（默认）| 综合向量检索 + 知识图谱，通用场景 |
| `local` | 答案集中在局部段落，精确匹配 |
| `global` | 全局主题、跨文档综合 |
| `naive` | 简单向量检索，速度最快 |

---

## EduAgent（教学助手）

EduAgent 使用独立配置，**不再**从 `rag_mvp.config` 读取 LLM 或工具密钥。入口（`edu chat`、定时任务等）通过 `edu_agent.config_loader.load_settings()` 装载配置。

### 配置文件

- 默认在**当前工作目录**查找 `edu_agent.yaml`；不存在时仍会从 `.env` 合并 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 等兼容字段。
- 可将仓库中的 [`edu_agent.yaml.example`](edu_agent.yaml.example) 复制为 `edu_agent.yaml` 并按需修改 `agent.provider`（如 `dashscope`、`ollama`、`deepseek`、`openai`）及 `providers` 下对应条目的 `api_key` / `base_url`。

### 启动对话

```powershell
uv sync
uv run edu chat
```

常用选项：`--user`、`--skills`（技能目录）、`--max-iter`。用户数据路径（会话、画像、缓存等）由 `agent.workspace` 与 [`EduPaths`](src/edu_agent/paths.py) 推导。

### 长期记忆（A3）

- 数据落在工作区 **`memory/`** 下（`facts/` JSONL、`concepts/`、`profiles/`，按用户隔离）。设计说明见 [`docs/phase3.md`](docs/phase3.md)（文末有与 [Hermes-agent](https://github.com/NousResearch/hermes-agent) 记忆分层的简要对照）；与 Hermes `MemoryManager` / `MemoryProvider` 的 API 与能力矩阵见 [`review_docs/hermes_memory_gap.md`](review_docs/hermes_memory_gap.md)。
- **运行时编排**：`MemoryCoordinator` 统一做检索上下文与阈值协整判断；`EduMemoryManager` 与内置的 `BuiltinFilesystemMemoryProvider` 在每轮对话前执行 `prefetch`（与 Hermes 的「单入口 + 至多一个外部槽位」心智一致；记忆类工具仍由 `src/edu_agent/tools/memory.py` 全局注册）。若开启向系统提示注入记忆片段，会对**非流式**完成后的助手正文做一次轻量清理，减轻模型回声复述注入标题的风险。
- **关闭记忆**：`uv run edu chat --disable-memory`（不提取、不协整、不注册记忆类工具）。
- **查看画像**：`uv run edu show-profile [--user default]`。

### 会话存储与恢复（SQLite）

- 会话数据仅存工作区根目录下的 **`sessions.db`**（结构化 Session / Message / ToolCall）。
- **恢复会话**：`uv run edu chat --session-id <uuid>`（会话 ID 在启动时打印；也可通过下列命令查看）。
- **列出最近会话**：`uv run edu list-sessions [--user default] [--limit 20]`。
- **清理旧会话**：`uv run edu cleanup-sessions --before 2025-01-01T00:00:00`（可加 `--archived-only`、`--yes` 跳过确认）。
- 超长对话会在超过上下文预算时由 `ContextManager` **自动**触发**压缩**（保留尾部若干轮与工具链；摘要失败时使用显式占位说明，见 `docs/phase2.md`）。在交互式 `edu chat` 中也可输入 **`/compress-context`** 或 **`/ctx-compress`** 手动触发一次压缩（调用 `EduAgent.trigger_context_compress()`，跳过「是否超阈值」判断，与网关卫生强制压缩同源；不影响长期记忆的阈值/退出协整）。

---

## 目录结构

```
.
├── src/rag_mvp/
│   ├── config.py      # RAG Worker 配置（pydantic-settings，读取 .env）
│   ├── llm.py         # Qwen LLM / Vision LLM / Embedding 函数
│   ├── engine.py      # RAGAnything 初始化与 ingest/query 封装
│   └── cli.py         # Click CLI 入口
├── src/edu_agent/     # 教学 Agent（edu_agent.yaml；memory/ 含 coordinator、manager、provider 等 A3 编排）
├── edu_agent.yaml.example  # EduAgent 配置模板
├── data/input/        # 放待处理文档（不提交到 git）
├── output/parsed/     # MinerU 解析输出（Markdown + JSON）
├── rag_storage/       # LightRAG 向量 + 知识图谱存储
├── .env.example       # 环境变量模板
├── pyproject.toml     # uv 项目配置
└── README.md
```

---

## 常见问题

**MinerU 模型下载失败**  
> 设置 `MINERU_SOURCE=huggingface` 并配置代理，或手动下载后设置 `MINERU_SOURCE=local`。

**DOCX / PPTX 解析报错**  
> 确认 LibreOffice 已安装且 `soffice` 在 PATH 中。

**CUDA 加速**  
> 将 `.env` 中 `MINERU_DEVICE=cpu` 改为 `cuda:0`，并确认已安装 CUDA 版 torch。

**Embedding 维度不匹配**  
> 切换 embedding 模型时，需删除 `rag_storage/` 目录后重新入库。
