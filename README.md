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

## 目录结构

```
.
├── src/rag_mvp/
│   ├── config.py      # 配置（pydantic-settings，读取 .env）
│   ├── llm.py         # Qwen LLM / Vision LLM / Embedding 函数
│   ├── engine.py      # RAGAnything 初始化与 ingest/query 封装
│   └── cli.py         # Click CLI 入口
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
