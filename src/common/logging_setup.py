"""Console + per-run file logging under data/logs/."""
from __future__ import annotations

import logging

from src.common import config
from src.common.timeutils import now_ist


def get_logger(job: str) -> logging.Logger:
    """Logger writing to stderr and data/logs/YYYY-MM-DD_<job>.log (IST date)."""
    logger = logging.getLogger(f"newspulse.{job}")
    if logger.handlers:  # already configured (idempotent re-runs in one process)
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOGS_DIR / f"{now_ist():%Y-%m-%d}_{job}.log"
    fileh = logging.FileHandler(logfile, encoding="utf-8")
    fileh.setFormatter(fmt)
    logger.addHandler(fileh)
    return logger
