"""Single long-lived asyncio loop for edu-rag-worker (LightRAG global locks are loop-bound).

Course ingest/delete and assignment generation must run on this loop when the worker
has started it, instead of calling ``asyncio.run`` per Redis task.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_start_lock = threading.Lock()


def is_worker_async_loop_started() -> bool:
    return bool(
        _thread is not None
        and _thread.is_alive()
        and _loop is not None
        and _loop.is_running(),
    )


def start_worker_async_loop() -> None:
    """Start the background thread and run ``loop.run_forever()`` (idempotent)."""
    global _loop, _thread
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return

        ready = threading.Event()
        exc_holder: list[BaseException] = []

        def _runner() -> None:
            global _loop
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                _loop = loop
                ready.set()
                loop.run_forever()
            except BaseException as exc:  # pragma: no cover - defensive
                exc_holder.append(exc)
                if not ready.is_set():
                    ready.set()
            finally:
                asyncio.set_event_loop(None)

        t = threading.Thread(target=_runner, name="edu-rag-worker-async", daemon=True)
        _thread = t
        t.start()
        if not ready.wait(timeout=60):
            raise RuntimeError("edu-rag-worker async loop thread did not start in time")
        if exc_holder:
            raise RuntimeError(f"edu-rag-worker async loop failed: {exc_holder[0]!r}") from exc_holder[0]
        if _loop is None or not _loop.is_running():
            raise RuntimeError("edu-rag-worker async loop is not running after thread start")


def stop_worker_async_loop() -> None:
    """Stop ``run_forever`` and join the thread (best-effort)."""
    global _loop, _thread
    with _start_lock:
        loop = _loop
        th = _thread
        _loop = None
        _thread = None
    if loop is None or th is None:
        return

    def _stop() -> None:
        loop.stop()

    try:
        loop.call_soon_threadsafe(_stop)
    except RuntimeError:
        pass
    th.join(timeout=120)


def run_worker_coroutine(
    coro: Coroutine[Any, Any, T],
    *,
    timeout: float | None = None,
) -> T:
    """Run *coro* on the worker loop from a synchronous thread; propagate exceptions."""
    if _loop is None or not _loop.is_running():
        raise RuntimeError("worker async loop is not running; call start_worker_async_loop() first")
    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    return fut.result(timeout=timeout)
