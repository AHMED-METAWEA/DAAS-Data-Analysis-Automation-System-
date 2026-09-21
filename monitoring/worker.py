"""Standalone monitoring worker.

    python -m monitoring.worker

The same scheduler the API process hosts, running on its own instead.  Two
reasons to use it:

* **The web process restarts.**  A deployment that redeploys the API at 07:00
  drops whatever was firing at 07:00.  A separate worker keeps its own uptime.
* **The web process scales.**  Three API replicas each hosting a scheduler is
  three attempts at every run — safe, because :func:`monitoring.runner.run_schedule`
  takes an advisory lock, but wasteful. One worker plus
  ``MONITORING_SCHEDULER=0`` on the web tier is the tidy arrangement.

Running both is safe. The advisory lock makes duplicate execution a skip, not a
double delivery — the design assumes someone will eventually do it by accident.
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

from dotenv import load_dotenv

from db.init_platform import ensure_platform_tables
from monitoring.scheduler import shutdown_scheduler, start_scheduler

logger = logging.getLogger("monitoring.worker")


def _install_signal_handlers() -> None:
    def _stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s — shutting the monitoring worker down", signum)
        shutdown_scheduler(wait=False)
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # The worker starts outside the API process, so nothing else has loaded the
    # environment for it. Without this every database and provider credential is
    # missing and the first run fails with a confusing connection error.
    load_dotenv()

    try:
        ensure_platform_tables()
    except Exception:
        logger.exception(
            "Could not reach Postgres. Is `docker compose up -d` running? The worker cannot "
            "read schedules without it."
        )
        return 1

    _install_signal_handlers()
    logger.info("Monitoring worker starting — press Ctrl+C to stop")
    try:
        start_scheduler(blocking=True)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        logger.info("Interrupted")
    finally:
        shutdown_scheduler(wait=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
