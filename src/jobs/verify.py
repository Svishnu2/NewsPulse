"""16:00 IST verify job — the scorekeeper.

Marks every prediction on m1 (gap), m2 (day direction), m3 (tradeable,
the primary metric), simulates one opening-range-breakout paper trade with
realistic fills (gap-through fills at candle open, circuit-lock handling,
oversized-gap skip), then recomputes lifetime stats and refreshes docs/data/.
"""
from __future__ import annotations

import asyncio
import shutil
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
from src.jobs.gapcheck import opening_range, price_at, session_slice

logger = get_logger("verify")


# --------------------------------------------------------------- simulation
def _is_locked(row: pd.Series, median_volume: float) -> bool:
    """Circuit-limit fingerprint: flat OHLC candle with near-zero volume."""
    flat = row["High"] == row["Low"] == row["Open"] == row["Close"]
    return bool(flat and row["Volume"] <= max(1.0, 0.001 * median_volume))


def _unlock_fill(after: pd.DataFrame, start_pos: int, median_volume: float,
                 fallback: float) -> tuple[float, str]:
    """First normally traded candle's open after a locked candle."""
    for pos in range(start_pos + 1, len(after)):
        row = after.iloc[pos]
        if not _is_locked(row, median_volume):
            return float(row["Open"]), str(after.index[pos])
    return fallback, str(after.index[-1])


def simulate_trade(
    session: pd.DataFrame, direction: str, gap_pct: float | None,
    or_high: float | None, or_low: float | None,
) -> dict[str, Any]:
    """Opening-range-breakout paper trade with realistic fills.

    Candles are walked strictly in time order. If a candle OPENS beyond the
    stop/target, the fill is that open (never the level) and the difference
    is recorded as slippage. |gap| > 4% skips the trade entirely.
    """
    no_entry = {"outcome": "NO_ENTRY", "pnl_pct": 0.0, "pnl_inr": 0.0}
    if gap_pct is None or or_high is None or or_low is None or session.empty:
        return {**no_entry, "reason": "no_data"}
    if abs(gap_pct) > config.MAX_GAP_FOR_ENTRY_PCT:
        return {**no_entry, "reason": "gap_too_large"}

    times = session.index.time
    lo, hi = time(*config.ENTRY_WINDOW[0]), time(*config.ENTRY_WINDOW[1])
    window = session[(times >= lo) & (times <= hi)]
    is_long = direction == "LONG"

    breakout = (
        window[window["Close"] > or_high] if is_long else window[window["Close"] < or_low]
    )
    if breakout.empty:
        return {**no_entry, "reason": "no_breakout"}

    entry_ts = breakout.index[0]
    entry = float(breakout["Close"].iloc[0])
    if is_long:
        stop = max(or_low, entry * (1 - config.STOP_PCT / 100))
        target = entry * (1 + config.TARGET_PCT / 100)
    else:
        stop = min(or_high, entry * (1 + config.STOP_PCT / 100))
        target = entry * (1 - config.TARGET_PCT / 100)

    after = session[session.index > entry_ts]
    median_volume = float(session["Volume"].median()) if "Volume" in session else 0.0
    forced = time(*config.FORCED_EXIT)

    exit_price: float | None = None
    exit_ts: str = ""
    exit_reason = "eod"
    slippage_pct = 0.0
    exit_quality = "normal"

    for pos, (ts, row) in enumerate(after.iterrows()):
        if ts.time() >= forced:
            exit_price, exit_ts, exit_reason = float(row["Open"]), str(ts), "time_exit"
            break
        o, h, low_ = float(row["Open"]), float(row["High"]), float(row["Low"])
        level: float | None = None
        if is_long:
            if o <= stop:
                exit_price, exit_reason, level = o, "stop_gap", stop
            elif low_ <= stop:
                exit_price, exit_reason, level = stop, "stop", stop
            elif o >= target:
                exit_price, exit_reason, level = o, "target_gap", target
            elif h >= target:
                exit_price, exit_reason, level = target, "target", target
        else:
            if o >= stop:
                exit_price, exit_reason, level = o, "stop_gap", stop
            elif h >= stop:
                exit_price, exit_reason, level = stop, "stop", stop
            elif o <= target:
                exit_price, exit_reason, level = o, "target_gap", target
            elif low_ <= target:
                exit_price, exit_reason, level = target, "target", target
        if exit_price is not None:
            exit_ts = str(ts)
            if level:
                slippage_pct = round((exit_price - level) / level * 100, 3)
            if _is_locked(row, median_volume):
                exit_price, exit_ts = _unlock_fill(after, pos, median_volume, exit_price)
                exit_quality = "circuit_risk"
            break

    if exit_price is None:  # data ended before 15:10 — close out on last candle
        exit_price = float(after["Close"].iloc[-1]) if not after.empty else entry
        exit_ts = str(after.index[-1]) if not after.empty else str(entry_ts)

    raw = (exit_price - entry) / entry * 100 if is_long else (entry - exit_price) / entry * 100
    pnl_pct = round(raw - config.ROUND_TRIP_COST_PCT, 2)
    return {
        "outcome": "WIN" if pnl_pct > 0 else "LOSS",
        "entry_time": str(entry_ts),
        "entry_price": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "exit_time": exit_ts,
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "slippage_pct": slippage_pct,
        "exit_quality": exit_quality,
        "gross_pct": round(raw, 2),
        "costs_pct": config.ROUND_TRIP_COST_PCT,
        "pnl_pct": pnl_pct,
        "pnl_inr": round(config.NOTIONAL_INR * pnl_pct / 100, 2),
    }


# ----------------------------------------------------------------- metrics
async def verify_one(pred: dict[str, Any], universe: Universe, today) -> None:
    symbol = pred["symbol"]
    stock = universe.by_symbol.get(symbol)
    if stock is None:
        pred["verification"] = {"error": "unknown_symbol", "verified_at": ist_iso()}
        return
    df, interval = await intraday(stock.yahoo, logger)
    pc = pred.get("gap_check", {}).get("prev_close") or await prev_close(
        stock.yahoo, today, logger
    )
    if df.empty or pc is None:
        pred["verification"] = {"error": "no_data", "verified_at": ist_iso()}
        logger.warning("%s: no data to verify", symbol)
        return

    session = session_slice(df)
    if session.empty:
        pred["verification"] = {"error": "no_session_data", "verified_at": ist_iso()}
        return

    is_long = pred["direction"] == "LONG"
    open_price = round(float(session["Open"].iloc[0]), 2)
    close_price = round(float(session["Close"].iloc[-1]), 2)
    day_high = round(float(session["High"].max()), 2)
    day_low = round(float(session["Low"].min()), 2)
    gap_pct = round((open_price - pc) / pc * 100, 2)
    or_high, or_low = opening_range(session)
    p0930 = price_at(session, 9, 30)
    p1510 = price_at(session, *config.FORCED_EXIT)

    m1 = open_price > pc if is_long else open_price < pc
    m2 = close_price > pc if is_long else close_price < pc
    m3 = None
    if p0930 and p1510:
        m3 = p1510 > p0930 if is_long else p1510 < p0930

    mfe = mae = None
    times = session.index.time
    active = session[times >= time(9, 30)]
    if p0930 and not active.empty:
        hi = float(active["High"].max())
        lo = float(active["Low"].min())
        if is_long:
            mfe = round((hi - p0930) / p0930 * 100, 2)
            mae = round((lo - p0930) / p0930 * 100, 2)
        else:
            mfe = round((p0930 - lo) / p0930 * 100, 2)
            mae = round((p0930 - hi) / p0930 * 100, 2)

    trade = simulate_trade(session, pred["direction"], gap_pct, or_high, or_low)
    pred["verification"] = {
        "prev_close": pc,
        "open": open_price,
        "high": day_high,
        "low": day_low,
        "close": close_price,
        "gap_pct": gap_pct,
        "price_0930": p0930,
        "price_1510": p1510,
        "interval": interval,
        "m1_gap": m1,
        "m2_day_direction": m2,
        "m3_tradeable": m3,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "trade": trade,
        "verified_at": ist_iso(),
    }
    logger.info("%s: m1=%s m2=%s m3=%s trade=%s %.2f%%", symbol, m1, m2, m3,
                trade["outcome"], trade.get("pnl_pct", 0.0))


# ------------------------------------------------------------------- stats
def _bucket(confidence: int) -> str:
    for lo, hi, label in config.CONFIDENCE_BUCKETS:
        if lo <= confidence < hi:
            return label
    return "other"


def _rate(records: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    scored = [r for r in records if r["verification"].get(metric) is not None]
    wins = sum(1 for r in scored if r["verification"][metric])
    return {
        "success": wins,
        "total": len(scored),
        "pct": round(wins / len(scored) * 100, 1) if scored else None,
    }


def _grouped(records: list[dict[str, Any]], metric: str, key_fn) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        groups.setdefault(key_fn(r), []).append(r)
    return {k: _rate(v, metric) for k, v in sorted(groups.items())}


def recompute_stats() -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    dates: list[str] = []
    for path in sorted(config.PREDICTIONS_DIR.glob("*.json")):
        payload = read_json(path)
        if not payload:
            continue
        day_records = [
            {**p, "date": payload["date"]}
            for p in payload.get("predictions", [])
            if p.get("verification") and not p["verification"].get("error")
        ]
        if day_records:
            dates.append(payload["date"])
            records.extend(day_records)

    metrics: dict[str, Any] = {}
    for metric in ("m1_gap", "m2_day_direction", "m3_tradeable"):
        metrics[metric] = {
            "overall": _rate(records, metric),
            "by_direction": _grouped(records, metric, lambda r: r["direction"]),
            "by_catalyst": _grouped(records, metric, lambda r: r.get("catalyst_type", "other")),
            "by_confidence": _grouped(records, metric, lambda r: _bucket(r["confidence"])),
        }

    trades = [
        {**r["verification"]["trade"], "date": r["date"],
         "entry_time": r["verification"]["trade"].get("entry_time", "")}
        for r in records
        if r["verification"].get("trade")
    ]
    entered = [t for t in trades if t["outcome"] in ("WIN", "LOSS")]
    entered.sort(key=lambda t: (t["date"], t.get("entry_time", "")))

    equity: list[dict[str, Any]] = []
    cum = 0.0
    for day in dates:
        day_pnl = sum(t["pnl_inr"] for t in entered if t["date"] == day)
        cum += day_pnl
        equity.append({"date": day, "day_pnl_inr": round(day_pnl, 2),
                       "cum_pnl_inr": round(cum, 2)})

    streak_type, streak = "none", 0
    for t in reversed(entered):
        outcome = "win" if t["outcome"] == "WIN" else "loss"
        if streak == 0:
            streak_type, streak = outcome, 1
        elif outcome == streak_type:
            streak += 1
        else:
            break

    last20 = dates[-20:]
    rolling = _rate([r for r in records if r["date"] in last20], "m3_tradeable")

    return {
        "generated_at": ist_iso(),
        "notional_per_trade_inr": config.NOTIONAL_INR,
        "total_predictions": len(records),
        "sessions": len(dates),
        "metrics": metrics,
        "simulated": {
            "trades": len(entered),
            "wins": sum(1 for t in entered if t["outcome"] == "WIN"),
            "losses": sum(1 for t in entered if t["outcome"] == "LOSS"),
            "no_entry": sum(1 for t in trades if t["outcome"] == "NO_ENTRY"),
            "total_pnl_pct": round(sum(t["pnl_pct"] for t in entered), 2),
            "total_pnl_inr": round(sum(t["pnl_inr"] for t in entered), 2),
            "equity_curve": equity,
        },
        "rolling_last20": {"sessions": last20, "m3": rolling},
        "streak": {"type": streak_type, "count": streak},
    }


# --------------------------------------------------------------- docs sync
def sync_docs() -> None:
    """Rebuild docs/data/ from data/ so GitHub Pages serves current numbers.
    Replaces everything (including any shipped demo data)."""
    if config.DOCS_DATA_DIR.exists():
        shutil.rmtree(config.DOCS_DATA_DIR)
    (config.DOCS_DATA_DIR / "predictions").mkdir(parents=True, exist_ok=True)

    shutil.copy2(config.STATS_JSON, config.DOCS_DATA_DIR / "stats.json")

    index: list[dict[str, Any]] = []
    for path in sorted(config.PREDICTIONS_DIR.glob("*.json"), reverse=True):
        payload = read_json(path)
        if not payload:
            continue
        shutil.copy2(path, config.DOCS_DATA_DIR / "predictions" / path.name)
        preds = payload.get("predictions", [])
        verified = [p for p in preds if p.get("verification")
                    and not p["verification"].get("error")]
        day_pnl = round(
            sum(
                p["verification"]["trade"].get("pnl_inr", 0.0)
                for p in verified
                if p["verification"].get("trade")
            ),
            2,
        )
        index.append({
            "date": payload["date"],
            "bias": payload.get("market_context", {}).get("bias", "neutral"),
            "picks": len(preds),
            "m3_success": sum(
                1 for p in verified if p["verification"].get("m3_tradeable")
            ),
            "m3_total": sum(
                1 for p in verified if p["verification"].get("m3_tradeable") is not None
            ),
            "day_pnl_inr": day_pnl,
            "status": payload.get("status", "pending"),
        })
    write_json(config.DOCS_DATA_DIR / "index.json", index)


# --------------------------------------------------------------------- run
async def run() -> int:
    today = today_ist()
    if not is_trading_day(today) and not config.FORCE_RUN:
        logger.info("%s is not a trading day — exiting quietly", today)
        return 0

    day_file = config.PREDICTIONS_DIR / f"{today.isoformat()}.json"
    payload = read_json(day_file)
    if payload and payload.get("predictions"):
        universe = Universe.load()
        await asyncio.gather(
            *(verify_one(p, universe, today) for p in payload["predictions"])
        )
        payload["status"] = "verified"
        write_json(day_file, payload)
        logger.info("Verified %d predictions for %s", len(payload["predictions"]), today)
    else:
        logger.info("No predictions for %s — refreshing stats/dashboard only", today)

    stats = recompute_stats()
    write_json(config.STATS_JSON, stats)
    sync_docs()
    logger.info(
        "Stats: %d predictions over %d sessions, m3 %s%%, sim P&L Rs %.0f",
        stats["total_predictions"], stats["sessions"],
        stats["metrics"]["m3_tradeable"]["overall"]["pct"],
        stats["simulated"]["total_pnl_inr"],
    )
    commit_and_push(f"verify {today}: stats + dashboard refresh", logger)
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except Exception:
        logger.error("Unhandled error:\n%s", traceback.format_exc())
        commit_and_push(f"verify {today_ist()}: failed run log", logger)
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
