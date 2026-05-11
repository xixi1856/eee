"""Extract audio from video (ffmpeg) and transcribe with faster-whisper."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from .config import Settings, settings

_VIDEO_SUFFIXES = frozenset({
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".mpeg",
    ".mpg",
    ".avi",
    ".m4v",
    ".wmv",
})
_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"})


def is_video_or_audio_file(path: Path) -> bool:
    suf = path.suffix.lower()
    return suf in _VIDEO_SUFFIXES or suf in _AUDIO_SUFFIXES


def _which_ffmpeg(ffmpeg_path: str) -> str:
    if Path(ffmpeg_path).is_file():
        return str(Path(ffmpeg_path).resolve())
    resolved = shutil.which(ffmpeg_path)
    if not resolved:
        raise RuntimeError(
            f"ffmpeg not found ({ffmpeg_path!r}). Install ffmpeg and/or set FFMPEG_PATH in .env."
        )
    return resolved


def extract_audio_with_ffmpeg(
    media: Path,
    work_dir: Path,
    *,
    ffmpeg_bin: str | None = None,
) -> Path:
    """Demux audio to 16 kHz mono PCM WAV for Whisper."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_wav = work_dir / "audio_for_whisper.wav"
    ff = _which_ffmpeg(ffmpeg_bin or settings.ffmpeg_path)
    cmd = [
        ff,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(out_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode}): {err or 'no stderr'}"
        )
    if not out_wav.exists() or out_wav.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no audio output (empty file or missing).")
    return out_wav


def transcribe_audio_to_text(
    audio: Path,
    *,
    model_size: str | None = None,
    device: str | None = None,
    language: str | None = None,
) -> str:
    """Run faster-whisper on a WAV (or other decodeable) path; returns plain text."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Run: uv sync --extra video"
        ) from exc

    cfg = settings
    ms = model_size if model_size is not None else cfg.whisper_model_size
    dev = device if device is not None else cfg.whisper_device
    lang = language if language is not None else (cfg.whisper_language or None)
    if lang == "":
        lang = None

    logger.info("Loading Whisper model {} on {}", ms, dev)
    model = WhisperModel(ms, device=dev)
    logger.info("Transcribing {}", audio)
    segments, info = model.transcribe(str(audio), language=lang)
    parts: list[str] = []
    for seg in segments:
        line = seg.text.strip()
        if line:
            parts.append(f"[{seg.start:.1f}s] {line}")
    text = "\n\n".join(parts)
    if not text.strip():
        logger.warning("Whisper returned empty text (detected language: {})", getattr(info, "language", "?"))
    return text


def write_transcript(stem: str, text: str, out_dir: Path) -> Path:
    """Write UTF-8 transcript next to other outputs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.txt"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def transcribe_media_to_txt_file(
    media: Path,
    *,
    cfg: Settings | None = None,
    transcript_dir: Path | None = None,
    work_parent: Path | None = None,
) -> Path:
    """Full path: video/audio → temp wav → whisper → ``transcript_dir/<stem>.txt``."""
    cfg = cfg or settings
    media = media.resolve()
    if not media.exists():
        raise FileNotFoundError(media)
    if not is_video_or_audio_file(media):
        raise ValueError(
            f"Unsupported media type {media.suffix!r}. "
            f"Use a video ({', '.join(sorted(_VIDEO_SUFFIXES))}) or audio file."
        )

    out_dir = (transcript_dir or cfg.transcript_output_dir).resolve()
    stem = media.stem

    if media.suffix.lower() in _AUDIO_SUFFIXES:
        audio_path = media
        lang = cfg.whisper_language.strip() if cfg.whisper_language else None
        text = transcribe_audio_to_text(
            audio_path,
            model_size=cfg.whisper_model_size,
            device=cfg.whisper_device,
            language=lang,
        )
        return write_transcript(stem, text, out_dir)

    tmp_root = Path(work_parent) if work_parent is not None else Path(tempfile.mkdtemp(prefix="rag_video_"))
    work = tmp_root / "work"
    try:
        wav = extract_audio_with_ffmpeg(media, work, ffmpeg_bin=cfg.ffmpeg_path)
        lang = cfg.whisper_language.strip() if cfg.whisper_language else None
        text = transcribe_audio_to_text(
            wav,
            model_size=cfg.whisper_model_size,
            device=cfg.whisper_device,
            language=lang,
        )
        return write_transcript(stem, text, out_dir)
    finally:
        if work_parent is None:
            import shutil as _sh

            _sh.rmtree(tmp_root, ignore_errors=True)
