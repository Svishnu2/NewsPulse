"""yfinance price access, wrapped for asyncio with a concurrency cap."""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import pandas as pd
import yfinance as yf

from src.common.timeutils import IST

_SEM = asyncio.Semaphore(4)


def _history(yahoo: str, **kwargs: object) -> pd.DataFrame:
    df = yf.Ticker(yahoo).history(**kwargs)
    if df is None or df.empty:
        return pd.DataFrame()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.tz_convert(IST)


async def intraday(yahoo: str, logger: logging.Logger) -> tuple[pd.DataFrame, str]:
    """Today's session candles with IST index. Returns (df, interval_used);
    falls back from 1-minute to 5-minute data, ('', 'none') style empty df on failure."""
    for interval in ("1m", "5m"):
        try:
            async with _SEM:
                df = await asyncio.to_thread(
                    _history, yahoo, period="1d", interval=interval, auto_adjust=False
                )
        except Exception as exc:
            logger.warning("intraday %s %s failed: %s", yahoo, interval, exc)
            df = pd.DataFrame()
        if not df.empty:
            return df, interval
    return pd.DataFrame(), "none"


async def prev_close(yahoo: str, session_date: date, logger: logging.Logger) -> float | None:
    """Close of the last daily bar strictly before session_date."""
    try:
        async with _SEM:
            df = await asyncio.to_thread(
                _history, yahoo, period="10d", interval="1d", auto_adjust=False
            )
    except Exception as exc:
        logger.warning("prev_close %s failed: %s", yahoo, exc)
        return None
    if df.empty:
        return None
    prior = df[[ts.date() < session_date for ts in df.index]]
    if prior.empty:
        return None
    return round(float(prior["Close"].iloc[-1]), 2)


async def turnover_crore(yahoo: str, logger: logging.Logger, days: int = 20) -> float | None:
    """Average daily turnover (Close x Volume) over the last `days` sessions, in Rs crore."""
    try:
        async with _SEM:
            df = await asyncio.to_thread(
                _history, yahoo, period="3mo", interval="1d", auto_adjust=False
            )
    except Exception as exc:
        logger.warning("turnover %s failed: %s", yahoo, exc)
        return None
    if df.empty or "Volume" not in df:
        return None
    tail = df.tail(days)
    turnover = (tail["Close"] * tail["Volume"]).mean()
    if pd.isna(turnover):
        return None
    return round(float(turnover) / 1e7, 2)  # 1 crore = 1e7
