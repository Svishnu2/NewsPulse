"""09:35 IST gap-check job: record the actual open vs. prediction.

For each pick: prev_close, open, gap %, 09:15-09:30 opening range,
price at 09:30, and whether the gap agreed with the predicted direction.
Idempotent — only writes/overwrites the gap_check block of each pick.
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import time
from typing import Any

import pandas as pd

from src.analysis.entity_match import Universe
from src.common import config
from src.common.logging_setup import get_logger
from src.common.storage import commit_and_push, read_json, write_json
from src.common.timeutils import ist_iso, is_trading_day, today_ist
from src.fetchers.prices import intraday, prev_close

logger = get_logger("gapcheck")


def session_slice(df: pd.DataFrame) -> pd.DataFrame:
    times = df.index.time
    lo, hi = time(*config.SESSION[0]), time(*config.SESSION[1])
    return df[(times >= lo) & (times <= hi)]


def opening_range(df: pd.DataFrame) -> tuple[float | None, float | None]:
    times = df.index.time
    lo, hi = time(*config.OPENING_RANGE[0]), time(*config.OPENING_RANGE[1])
    window = df[(times >= lo) & (times < hi)]
    if window.empty:
        return None, None
    return round(float(window["High"].max()), 2), round(float(window["Low"].min()), 2)


def price_at(df: pd.DataFrame, hh: int, mm: int) -> float | None:
    """Open of the first candle at/after hh:mm; falls back to last close before it."""
    times = df.index.time
    after = df[times >= time(hh, mm)]
    if not after.empty:
        return round(float(after["Open"].iloc[0]), 2)
    before = df[times < time(hh, mm)]
    if not before.empty:
        return round(float(before["Close"].iloc[-1]), 2)
    return None


async def check_one(pred: dict[str, Any], universe: Universe, today) -> None:
    symbol = pred["symbol"]
    stock = universe.by_symbol.get(symbol)
    if stock is None:
        logger.warning("%s not in universe file — skipping", symbol)
        return
    df, interval = await intraday(stock.yahoo, logger)
    pc = await prev_close(stock.yahoo, today, logger)
    if df.empty or pc is None:
        logger.warning("%s: no intraday data or prev close — skipping", symbol)
        pred["gap_check"] = {"error": "no_data", "checked_at": ist_iso()}
        return
    session = session_slice(df)
    if session.empty:
        pred["gap_check"] = {"error": "no_session_data", "checked_at": ist_iso()}
        return

    open_price = round(float(session["Open"].iloc[0]), 2)
    gap_pct = round((open_price - pc) / pc * 100, 2)
    or_high, or_low = opening_range(session)
    p0930 = price_at(session, 9, 30)
    gap_agrees = gap_pct > 0 if pred["direction"] == "LONG" else gap_pct < 0
    pred["gap_check"] = {
        "prev_close": pc,
        "open": open_price,
        "gap_pct": gap_pct,
        "or_high": or_high,
        "or_low": or_low,
        "price_0930": p0930,
        "gap_agrees": gap_agrees,
        "interval": interval,
        "checked_at": ist_iso(),
    }
    logger.info("%s: gap %+.2f%% (agrees=%s), OR %s-%s", symbol, gap_pct, gap_agrees,
                or_low, or_high)


async def run() -> int:
    today = today_ist()
    if not is_trading_day(today) and not config.FORCE_RUN:
        logger.info("%s is not a trading day — exiting quietly", today)
        return 0

    day_file = config.PREDICTIONS_DIR / f"{today.isoformat()}.json"
    payload = read_json(day_file)
    if not payload:
        logger.info("No prediction file for %s — nothing to gap-check", today)
        return 0
    predictions = payload.get("predictions", [])
    if not predictions:
        logger.info("Prediction file has no picks — nothing to do")
        return 0

    universe = Universe.load()
    await asyncio.gather(*(check_one(p, universe, today) for p in predictions))

    if payload.get("status") == "pending":
        payload["status"] = "gap_checked"
    write_json(day_file, payload)
    commit_and_push(f"gapcheck {today}: {len(predictions)} picks", logger)
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except Exception:
        logger.error("Unhandled error:\n%s", traceback.format_exc())
        commit_and_push(f"gapcheck {today_ist()}: failed run log", logger)
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
