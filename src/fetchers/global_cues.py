"""Overnight global market cues via yfinance, fetched concurrently.

GIFT Nifty has no reliable free feed, so the derived market bias comes
from US futures + Asian indices + the US close, weighted.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd
import yfinance as yf

from src.common.timeutils import IST

# key -> (yahoo ticker, display name, bias weight; 0 = context only)
CUE_TICKERS: dict[str, tuple[str, str, float]] = {
    "sp500": ("^GSPC", "S&P 500", 0.5),
    "nasdaq": ("^IXIC", "Nasdaq", 0.5),
    "dow_futures": ("YM=F", "Dow futures", 1.5),
    "nasdaq_futures": ("NQ=F", "Nasdaq futures", 2.0),
    "nikkei": ("^N225", "Nikkei 225", 1.0),
    "hang_seng": ("^HSI", "Hang Seng", 1.0),
    "brent": ("BZ=F", "Brent crude", 0.0),
    "gold": ("GC=F", "Gold", 0.0),
    "usdinr": ("INR=X", "USD/INR", 0.0),
}

BIAS_THRESHOLD_PCT = 0.25


def _last_change(yahoo: str) -> dict[str, float] | None:
    df: pd.DataFrame = yf.Ticker(yahoo).history(period="5d", interval="1d", auto_adjust=False)
    if df is None or len(df) < 2:
        return None
    last = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2])
    if prev == 0:
        return None
    return {"last": round(last, 2), "change_pct": round((last - prev) / prev * 100, 2)}


async def fetch_global_cues(logger: logging.Logger) -> dict[str, Any]:
    """Return {key: {name, last, change_pct} | None, ...} plus derived bias."""

    async def one(key: str, yahoo: str, name: str) -> tuple[str, dict[str, Any] | None]:
        try:
            data = await asyncio.to_thread(_last_change, yahoo)
        except Exception as exc:  # yfinance raises many flavours; never crash
            logger.warning("Cue %s (%s) failed: %s", key, yahoo, exc)
            data = None
        if data is not None:
            data["name"] = name
        return key, data

    pairs = await asyncio.gather(
        *(one(k, t, n) for k, (t, n, _) in CUE_TICKERS.items())
    )
    cues: dict[str, Any] = dict(pairs)

    score, weight_sum = 0.0, 0.0
    for key, (_, _, weight) in CUE_TICKERS.items():
        if weight > 0 and cues.get(key):
            score += weight * cues[key]["change_pct"]
            weight_sum += weight
    if weight_sum > 0:
        avg = score / weight_sum
        bias = "bullish" if avg >= BIAS_THRESHOLD_PCT else (
            "bearish" if avg <= -BIAS_THRESHOLD_PCT else "neutral"
        )
        cues["derived_bias"] = bias
        cues["bias_score"] = round(avg, 3)
    else:
        cues["derived_bias"] = "neutral"
        cues["bias_score"] = 0.0
    cues["note"] = "GIFT Nifty unavailable on free feeds; bias from futures + Asia."
    logger.info("Global cues: bias=%s score=%.3f", cues["derived_bias"], cues["bias_score"])
    return cues


def cues_summary_lines(cues: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, (_, name, _) in CUE_TICKERS.items():
        item = cues.get(key)
        if item:
            lines.append(f"{name}: {item['change_pct']:+.2f}% (last {item['last']})")
        else:
            lines.append(f"{name}: unavailable")
    lines.append(f"Derived overnight bias: {cues.get('derived_bias', 'neutral')}")
    return lines


# tz re-export so jobs can slice cue frames consistently if ever needed
__all__ = ["fetch_global_cues", "cues_summary_lines", "CUE_TICKERS", "IST"]
