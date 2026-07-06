"""JSON read/write plus git commit+push back to the repo (GitHub Actions)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from src.common import config


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)


def _git(args: list[str], logger: logging.Logger) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args], cwd=config.ROOT, capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        logger.debug("git %s -> %s | %s", " ".join(args), proc.returncode, proc.stderr.strip())
    return proc


def commit_and_push(message: str, logger: logging.Logger) -> bool:
    """Stage data/ + docs/, commit, and push with rebase-retry.

    Pushes only inside GitHub Actions (or when NEWSPULSE_FORCE_COMMIT=1);
    local runs just log what would have been committed.
    """
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    forced = os.environ.get("NEWSPULSE_FORCE_COMMIT") == "1"
    if not in_ci and not forced:
        logger.info("Local run: skipping git commit ('%s')", message)
        return False

    _git(["add", "data", "docs"], logger)
    if _git(["diff", "--cached", "--quiet"], logger).returncode == 0:
        logger.info("Nothing new to commit.")
        return True

    ident = [
        "-c",
        "user.name=newspulse-bot",
        "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
    ]
    if _git([*ident, "commit", "-m", message], logger).returncode != 0:
        logger.error("git commit failed")
        return False

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], logger).stdout.strip() or "main"
    for attempt in range(1, 4):
        _git(["pull", "--rebase", "origin", branch], logger)
        if _git(["push", "origin", branch], logger).returncode == 0:
            logger.info("Pushed: %s", message)
            return True
        logger.warning("Push attempt %d failed; retrying", attempt)
        time.sleep(5 * attempt)
    logger.error("All push attempts failed for '%s'", message)
    return False
