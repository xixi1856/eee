"""Configuration loaded from environment / .env file."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM API (DashScope / Qwen)
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # Model names
    llm_model: str = "qwen-plus-2025-04-28"
    refine_model: str = "qwen-long"   # long-context model for structure-refine phase
    vision_model: str = "qwen-vl-max"
    # Ollama embeddings (LightRAG ollama_embed); e.g. ollama pull bge-m3
    ollama_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "bge-m3"
    # Must match Ollama model output and PostgreSQL ``vector(N)`` on LightRAG tables (Phase 7: 1024).
    embedding_dim: int = 1024
    embedding_max_tokens: int = 8192

    # LLM generation
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.1

    # Paths
    working_dir: Path = Path("rag_storage")
    output_dir: Path = Path("output/parsed")
    transcript_output_dir: Path = Path("output/transcripts")

    # Video → Whisper (CLI `rag video-ingest`; requires `uv sync --extra video` + ffmpeg)
    ffmpeg_path: str = "ffmpeg"
    whisper_model_size: str = "base"
    whisper_device: str = "cpu"
    # Empty string = auto language detection in faster-whisper
    whisper_language: str = ""

    # Video structured summary (before RAG ingest; LLM segments → .summary.md)
    video_summary_target_segment_seconds: int = 300
    video_summary_max_segment_seconds: int = 600
    # Empty = use refine_model for per-chunk JSON summaries
    video_summary_llm_model: str = ""
    # Empty = use built-in prompt in video_transcript_summary.py
    video_summary_system_prompt: str = ""

    # MinerU
    mineru_backend: str = "pipeline"
    mineru_device: str = "cpu"
    mineru_source: str = "modelscope"
    mineru_lang: str = "ch"

    # RAGAnything
    parser: str = "mineru"
    parse_method: str = "auto"

    # Proxy (used by wikipedia tool; leave empty to disable)
    http_proxy: str = ""

    # Concurrency limits (lower to reduce 429 rate-limit errors)
    llm_max_async: int = 4          # parallel LLM (chat) requests
    embedding_max_async: int = 16    # parallel embedding requests
    max_parallel_insert: int = 2    # parallel document inserts

    # Image filtering (skip decorative / useless images before full vision analysis)
    enable_image_filter: bool = False
    # Prompt sent to vision model for the quick filter check (binary USEFUL/USELESS)
    image_filter_prompt: str = (
        "判断这张图片是否包含有实质信息（如图表、流程图、表格、电路图、技术示意图等）。"
        "如果图片是装饰性图片、空白页面、纯色背景、水印、页眉页脚图标，或者截取部分无法传达任何实质信息的图，回答 USELESS。"
        "只有包含完整或可理解的图表、流程图、表格、电路图或技术示意图才回答 USEFUL。"
        "只回答 USEFUL 或 USELESS，不要有其他任何内容。"
    )

    # Graph storage backend: "NetworkXStorage" (no extra deps) or
    # "PGGraphStorage" (requires Apache AGE PostgreSQL extension)
    graph_storage: str = "NetworkXStorage"

    ollama_api_key: str = ""
    tavily_api_key: str = ""


settings = Settings()
