"""Process entry: HTTP Gateway (FastAPI + uvicorn) with graceful shutdown."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from edu_agent.agent import EduAgent
from edu_agent.auth.checker import AuthorizationChecker
from edu_agent.channels.registry import register_channel_adapters
from edu_agent.config_loader import load_settings
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.paths import build_paths
from edu_agent.runner.gateway import Gateway
from edu_agent.sessions.store import SessionStore
from edu_agent.types import AgentConfig

logger = logging.getLogger(__name__)


async def _async_main(host: str | None, port: int | None) -> None:
    settings = load_settings()
    gw_raw = settings.runtime.gateway or {}
    eff_host = host or str(gw_raw.get("host", "127.0.0.1"))
    eff_port = int(port if port is not None else gw_raw.get("port", 8765))
    paths = build_paths(settings)
    store = SessionStore(paths.sessions_db)
    seed = EduAgent(AgentConfig(), settings=settings, session_store=store)
    cm = ContextManager(
        store,
        ContextConfig(model_max_tokens=seed._max_tokens),
        settings,
        model_name=seed._model,
        summarizer=seed._build_summarizer(),
    )
    del seed

    gateway = Gateway(
        settings=settings,
        session_store=store,
        context_manager=cm,
        auth_checker=AuthorizationChecker(
            expected_api_key=str(gw_raw.get("api_key") or "").strip() or None
        ),
        queue_maxsize=int(gw_raw.get("queue_maxsize", 100)),
        outbound_queue_maxsize=int(gw_raw.get("outbound_queue_maxsize", 256)),
        runner_idle_timeout_sec=float(gw_raw.get("runner_idle_timeout_sec", 1800.0)),
        max_runners=int(gw_raw.get("max_runners", 256)),
        require_http_key=bool(gw_raw.get("require_http_key", False)),
    )
    register_channel_adapters(
        gateway,
        settings=settings,
        paths=paths,
        session_store=store,
        host=eff_host,
        port=eff_port,
    )

    stop = asyncio.Event()

    def _request_stop() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    if sys.platform == "win32":
        try:
            signal.signal(signal.SIGINT, lambda *_: _request_stop())
            signal.signal(signal.SIGTERM, lambda *_: _request_stop())
        except ValueError:
            pass
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                pass

    # Start CronDaemon if enabled in yaml (runtime.cron.enabled: true)
    cron_cfg = settings.runtime.cron
    if cron_cfg.get("enabled", False):
        from edu_agent.cron import CronDaemon
        _cron_daemon = CronDaemon()
        logger.info("CronDaemon started")

    await gateway.start()
    logger.info("EduAgent HTTP listening on http://%s:%s", eff_host, eff_port)
    await stop.wait()
    await gateway.stop()
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="EduAgent HTTP Gateway")
    parser.add_argument("--host", default=None, help="Bind host (default: yaml runtime.gateway.host)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: yaml runtime.gateway.port)")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level for gateway process.",
    )
    args = parser.parse_args()
    env_level = str(os.getenv("EDU_AGENT_LOG_LEVEL", "")).strip().upper()
    level_name = str(args.log_level or env_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_async_main(args.host, args.port))
    except KeyboardInterrupt:
        pass
