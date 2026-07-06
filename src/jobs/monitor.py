"""Optional market-hours monitor (09:10-15:30 IST, one long job).

Every ~3 minutes it re-fetches prices for today's picks and re-polls RSS.
Telegram alerts fire when a pick (a) moves >=1.5% within 5 minutes,
(b) crosses its simulated stop/target level, or (c) appears in a fresh
matched headline. Read-only: never trades, never touches verification data.
Any loop error is logged and the loop continues.
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
from src.common.notify import send_telegram, telegram_enabled
from src.common.storage import read_json
from src.common.timeutils import is_trading_day, news_cutoff, now_ist, today_ist
from src.fetchers.prices import intraday
from src.fetchers.rss_news import fetch_all_news, title_hash
from src.jobs.gapcheck import opening_range, session_slice

logger = get_logger("monitor")

POLL_SECONDS = 180
FAST_MOVE_PCT = 1.5
SESSION_END = time(15, 30)


def _levels(direction: str, or_high: float, or_low: float) -> tuple[float, float]:
    """Approximate the simulated trade's stop/target off the opening range."""
    if direction == "LONG":
        entry_ref = or_high
        return max(or_low, entry_ref * (1 - config.STOP_PCT / 100)), entry_ref * (
            1 + config.TARGET_PCT / 100
        )
    entry_ref = or_low
    return min(or_high, entry_ref * (1 + config.STOP_PCT / 100)), entry_ref * (
        1 - config.TARGET_PCT / 100
    )


async def _check_prices(
    picks: list[dict[str, Any]], universe: Universe, alerted: set[str]
) -> list[str]:
    alerts: list[str] = []
    for pick in picks:
        symbol = pick["symbol"]
        stock = universe.by_symbol.get(symbol)
        if stock is None:
            continue
        df, _ = await intraday(stock.yahoo, logger)
        session = session_slice(df) if not df.empty else pd.DataFrame()
        if session.empty:
            continue
        last = float(session["Close"].iloc[-1])

        # (a) fast move: last price vs ~5 minutes earlier
        if len(session) >= 6:
            ref = float(session["Close"].iloc[-6])
            move = (last - ref) / ref * 100
            key = f"{symbol}:fast:{'up' if move > 0 else 'down'}"
            if abs(move) >= FAST_MOVE_PCT and key not in alerted:
                alerted.add(key)
                alerts.append(f"{symbol}: sudden move {move:+.2f}% in ~5 min (now {last:.2f})")

        # (b) stop/target level crossings (opening-range based)
        gap_check = pick.get("gap_check", {})
        or_high = gap_check.get("or_high")
        or_low = gap_check.get("or_low")
        if (or_high is None or or_low is None) and now_ist().time() >= time(9, 31):
            or_high, or_low = opening_range(session)
        if or_high and or_low:
            stop, target = _levels(pick["direction"], or_high, or_low)
            hit_target = last >= target if pick["direction"] == "LONG" else last <= target
            hit_stop = last <= stop if pick["direction"] == "LONG" else last >= stop
            if hit_target and f"{symbol}:target" not in alerted:
                alerted.add(f"{symbol}:target")
                alerts.append(f"{symbol}: crossed simulated TARGET {target:.2f} (now {last:.2f})")
            elif hit_stop and f"{symbol}:stop" not in alerted:
                alerted.add(f"{symbol}:stop")
                alerts.append(f"{symbol}: crossed simulated STOP {stop:.2f} (now {last:.2f})")
    return alerts


async def _check_news(
    picks: list[dict[str, Any]], universe: Universe, seen: set[str]
) -> list[str]:
    cutoff = news_cutoff(today_ist())
    headlines = await fetch_all_news(cutoff, logger)
    symbols = {p["symbol"] for p in picks}
    alerts: list[str] = []
    for headline in headlines:
        key = title_hash(headline.title)
        if key in seen:
            continue
        seen.add(key)
        matched = universe.symbols_in(headline.text) & symbols
        for symbol in matched:
            alerts.append(f"{symbol}: fresh headline — {headline.title} ({headline.source})")
    return alerts


async def run() -> int:
    if not telegram_enabled():
        logger.info("Telegram secrets not set — monitor has nothing to do, exiting")
        return 0
    today = today_ist()
    if not is_trading_day(today) and not config.FORCE_RUN:
        logger.info("%s is not a trading day — exiting quietly", today)
        return 0

    payload = read_json(config.PREDICTIONS_DIR / f"{today.isoformat()}.json")
    picks = (payload or {}).get("predictions", [])
    if not picks:
        logger.info("No picks today — monitor exiting")
        return 0

    universe = Universe.load()
    alerted: set[str] = set()
    seen_titles: set[str] = set()
    logger.info("Monitoring %d picks until %s IST", len(picks), SESSION_END)

    # Prime seen_titles so we only alert on genuinely fresh headlines
    try:
        for h in await fetch_all_news(news_cutoff(today), logger):
            seen_titles.add(title_hash(h.title))
    except Exception as exc:
        logger.warning("Initial news prime failed: %s", exc)

    while now_ist().time() < SESSION_END:
        try:
            # refresh picks in case gapcheck merged opening-range data
            payload = read_json(config.PREDICTIONS_DIR / f"{today.isoformat()}.json")
            picks = (payload or {}).get("predictions", picks)

            alerts = await _check_prices(picks, universe, alerted)
            alerts += await _check_news(picks, universe, seen_titles)
            if alerts:
                text = "NewsPulse intraday alert\n" + "\n".join(alerts[:20])
                text += "\nEducational research tool. Not investment advice."
                await send_telegram(text, logger)
        except Exception:
            logger.error("Monitor loop error (continuing):\n%s", traceback.format_exc())
        await asyncio.sleep(POLL_SECONDS)

    logger.info("Session over — monitor exiting")
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except Exception:
        logger.error("Unhandled error:\n%s", traceback.format_exc())
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
