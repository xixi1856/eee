"""Cron scheduling layer for EduAgent.

Supports simple schedule expressions:
  - "every 30m"  / "every 2h" / "every 1d"
  - Standard 5-field cron:  "0 9 * * *"  (minute hour dom month dow)

Job state persists in data/cron_jobs.json.
The CronDaemon runs as a background daemon thread and ticks every 60 seconds.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_JOBS_FILE = Path("data") / "cron_jobs.json"
_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Schedule parsing helpers
# ---------------------------------------------------------------------------

_EVERY_RE = re.compile(r"^every\s+(\d+)\s*(m|min|minute|h|hour|d|day)s?$", re.IGNORECASE)


def _parse_interval_seconds(schedule: str) -> int | None:
    """Parse 'every Xm/Xh/Xd' → seconds, or None if not this format."""
    m = _EVERY_RE.match(schedule.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("m", "min", "minute"):
        return n * 60
    if unit in ("h", "hour"):
        return n * 3600
    if unit in ("d", "day"):
        return n * 86400
    return None


def _cron_next(cron_expr: str, after: datetime) -> datetime:
    """Return the next datetime after *after* matching a 5-field cron expression.

    Supports '*' wildcards and simple integers only.  Raises ValueError for
    unsupported expressions (use interval format instead).
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Unsupported cron expression: {cron_expr}")

    def _match(field: str, value: int) -> bool:
        return field == "*" or int(field) == value

    dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(525600):  # max 1 year of minutes
        if (
            _match(parts[0], dt.minute)
            and _match(parts[1], dt.hour)
            and _match(parts[2], dt.day)
            and _match(parts[3], dt.month)
            and _match(parts[4], dt.weekday())
        ):
            return dt
        dt += timedelta(minutes=1)
    raise ValueError("Could not compute next run for cron expression")


def compute_next_run(schedule: str, after: datetime | None = None) -> datetime:
    """Return the next scheduled datetime for *schedule*."""
    after = after or datetime.now()
    interval = _parse_interval_seconds(schedule)
    if interval is not None:
        return after + timedelta(seconds=interval)
    # Try cron expression
    return _cron_next(schedule, after)


def validate_schedule(schedule: str) -> str | None:
    """Return None if valid, or an error message string."""
    try:
        compute_next_run(schedule)
        return None
    except (ValueError, Exception) as exc:
        return str(exc)


# ---------------------------------------------------------------------------
# CronJob data model
# ---------------------------------------------------------------------------

@dataclass
class CronJob:
    id: str
    prompt: str
    schedule: str
    created_at: str
    status: str = "active"          # active | paused
    last_run: str = ""              # ISO datetime string
    next_run: str = ""              # ISO datetime string
    output_dir: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CronJob":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# CronManager
# ---------------------------------------------------------------------------

class CronManager:
    """Thread-safe CRUD for CronJob persistence."""

    def __init__(self, jobs_file: Path = _JOBS_FILE) -> None:
        self._file = jobs_file

    def _load(self) -> list[CronJob]:
        if not self._file.exists():
            return []
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            return [CronJob.from_dict(d) for d in raw]
        except Exception as exc:
            logger.warning("Failed to load cron jobs: %s", exc)
            return []

    def _save(self, jobs: list[CronJob]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps([j.to_dict() for j in jobs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_job(self, prompt: str, schedule: str) -> dict:
        err = validate_schedule(schedule)
        if err:
            raise ValueError(f"无效的调度表达式: {err}")
        job_id = uuid.uuid4().hex[:8]
        now = datetime.now()
        next_run = compute_next_run(schedule, now)
        output_dir = str(Path("output") / "cron" / job_id)
        job = CronJob(
            id=job_id,
            prompt=prompt,
            schedule=schedule,
            created_at=now.isoformat(timespec="seconds"),
            next_run=next_run.isoformat(timespec="seconds"),
            output_dir=output_dir,
        )
        with _LOCK:
            jobs = self._load()
            jobs.append(job)
            self._save(jobs)
        logger.info("Cron job created: %s (%s)", job_id, schedule)
        return job.to_dict()

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self._load()]

    def delete_job(self, job_id: str) -> bool:
        with _LOCK:
            jobs = self._load()
            new_jobs = [j for j in jobs if j.id != job_id]
            if len(new_jobs) == len(jobs):
                return False
            self._save(new_jobs)
        logger.info("Cron job deleted: %s", job_id)
        return True

    def get_due_jobs(self) -> list[CronJob]:
        """Return jobs whose next_run is in the past and status is active."""
        now = datetime.now()
        due = []
        for j in self._load():
            if j.status != "active" or not j.next_run:
                continue
            try:
                if datetime.fromisoformat(j.next_run) <= now:
                    due.append(j)
            except ValueError:
                pass
        return due

    def mark_ran(self, job_id: str) -> None:
        with _LOCK:
            jobs = self._load()
            for j in jobs:
                if j.id == job_id:
                    now = datetime.now()
                    j.last_run = now.isoformat(timespec="seconds")
                    j.next_run = compute_next_run(j.schedule, now).isoformat(timespec="seconds")
                    break
            self._save(jobs)

    def trigger_job(self, job_id: str) -> str:
        """Immediately execute a job and return a result summary."""
        jobs_by_id = {j.id: j for j in self._load()}
        if job_id not in jobs_by_id:
            return f"未找到任务: {job_id}"
        job = jobs_by_id[job_id]
        return _run_job(job)


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _run_job(job: CronJob) -> str:
    """Run a cron job in the current thread.  Returns a summary string."""
    from edu_agent.safety import check_input

    safety = check_input(job.prompt)
    if not safety.safe:
        logger.warning("Cron job %s blocked by safety filter: %s", job.id, safety.categories)
        return f"[安全拦截] 任务 {job.id} 被安全过滤器阻止: {safety.categories}"

    try:
        from edu_agent.agent import EduAgent
        from edu_agent.types import AgentConfig

        cfg = AgentConfig(
            session_id=f"cron_{job.id}_{int(time.time())}",
            max_iterations=15,
        )
        agent = EduAgent(config=cfg)
        reply = agent.run_turn(job.prompt)

        # Save output
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(job.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{ts}.md"
        out_file.write_text(
            f"# Cron Job {job.id} — {ts}\n\n**Prompt:** {job.prompt}\n\n---\n\n{reply}",
            encoding="utf-8",
        )
        logger.info("Cron job %s completed → %s", job.id, out_file)
        return f"任务 {job.id} 执行完毕，结果已保存至 {out_file}"
    except Exception as exc:
        logger.error("Cron job %s failed: %s", job.id, exc)
        return f"任务 {job.id} 执行失败: {exc}"


# ---------------------------------------------------------------------------
# CronDaemon
# ---------------------------------------------------------------------------

_SEMAPHORE = threading.Semaphore(2)  # max 2 concurrent cron jobs


class CronDaemon:
    """Background daemon thread that ticks every 60 seconds."""

    def __init__(self, manager: CronManager | None = None, tick_interval: int = 60) -> None:
        self._manager = manager or CronManager()
        self._interval = tick_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CronDaemon")
        self._thread.start()
        logger.info("CronDaemon started (tick=%ds)", self._interval)

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.error("CronDaemon tick error: %s", exc)
            self._stop_event.wait(timeout=self._interval)

    def _tick(self) -> None:
        due = self._manager.get_due_jobs()
        for job in due:
            self._manager.mark_ran(job.id)  # update next_run before executing
            t = threading.Thread(target=self._execute, args=(job,), daemon=True)
            t.start()

    def _execute(self, job: CronJob) -> None:
        with _SEMAPHORE:
            _run_job(job)
