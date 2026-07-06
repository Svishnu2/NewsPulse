"""IST time helpers and NSE trading-calendar checks."""
from __future__ import annotations

import csv
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from src.common import config

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def today_ist() -> date:
    return now_ist().date()


def ist_iso(dt: datetime | None = None) -> str:
    """ISO-8601 string in IST, second precision."""
    dt = dt or now_ist()
    return dt.astimezone(IST).isoformat(timespec="seconds")


def at_ist(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=IST)


def struct_to_ist(st: _time.struct_time) -> datetime:
    """feedparser gives UTC struct_time; convert to aware IST datetime."""
    return datetime(*st[:6], tzinfo=timezone.utc).astimezone(IST)


@lru_cache(maxsize=1)
def nse_holidays() -> frozenset[date]:
    """All dates from data/nse_holidays_*.csv (column 'date', YYYY-MM-DD)."""
    days: set[date] = set()
    for path in sorted(config.DATA_DIR.glob("nse_holidays_*.csv")):
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                raw = (row.get("date") or "").strip()
                if raw:
                    days.add(date.fromisoformat(raw))
    return frozenset(days)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nse_holidays()


def previous_trading_day(d: date) -> date:
    cur = d - timedelta(days=1)
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def news_cutoff(d: date) -> datetime:
    """News window start: previous trading day 15:30 IST."""
    return at_ist(previous_trading_day(d), 15, 30)
