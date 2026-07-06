"""Central configuration for NewsPulse. Every tunable lives here."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PREDICTIONS_DIR = DATA_DIR / "predictions"
LOGS_DIR = DATA_DIR / "logs"
DOCS_DIR = ROOT / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"

NIFTY200_CSV = DATA_DIR / "nifty200.csv"
EVENT_SECTOR_CSV = DATA_DIR / "event_sector_map.csv"
STATS_JSON = DATA_DIR / "stats.json"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


_load_dotenv()

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
FORCE_RUN: bool = os.environ.get("FORCE_RUN", "") == "1"

# ---------------------------------------------------------------- filters
MIN_CONFIDENCE = 60
MIN_TURNOVER_CRORE = 25.0  # 20-day average daily turnover floor, in Rs crore
MAX_PER_SECTOR = 2
MAX_PICKS = 6
LATE_START_CUTOFF = (9, 10)  # premarket aborts if it starts after 09:10 IST

# ------------------------------------------------------ simulated trading
NOTIONAL_INR = 10_000  # equal notional per simulated trade
TARGET_PCT = 1.5
STOP_PCT = 1.0
ROUND_TRIP_COST_PCT = 0.10
MAX_GAP_FOR_ENTRY_PCT = 4.0  # skip the paper trade beyond this open gap
OPENING_RANGE = ((9, 15), (9, 30))  # candles labelled [09:15, 09:30)
ENTRY_WINDOW = ((9, 31), (14, 30))
FORCED_EXIT = (15, 10)
SESSION = ((9, 15), (15, 30))

CONFIDENCE_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (60, 70, "60-70"),
    (70, 85, "70-85"),
    (85, 101, "85+"),
)

# ------------------------------------------------------------------- news
# Feeds with enabled=True returned HTTP 200 at build time (2026-07-05).
# Moneycontrol / Business Standard RSS returned 403 to non-browser clients
# even with full browser headers — left here disabled; flip enabled to True
# if they become reachable from your runner. Their stories still arrive via
# the Google News queries below. The fetcher tolerates any feed failing.
RSS_FEEDS: list[dict[str, str | bool]] = [
    {
        "name": "ET Markets",
        "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "enabled": True,
    },
    {
        "name": "ET Stocks",
        "url": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        "enabled": True,
    },
    {
        "name": "Livemint Markets",
        "url": "https://www.livemint.com/rss/markets",
        "enabled": True,
    },
    {
        "name": "Moneycontrol Buzzing",
        "url": "https://www.moneycontrol.com/rss/buzzingstocks.xml",
        "enabled": False,
    },
    {
        "name": "Moneycontrol Markets",
        "url": "https://www.moneycontrol.com/rss/marketreports.xml",
        "enabled": False,
    },
    {
        "name": "Business Standard Markets",
        "url": "https://www.business-standard.com/rss/markets-106.rss",
        "enabled": False,
    },
]

GOOGLE_NEWS_QUERIES: list[str] = [
    "nifty stocks",
    "indian stock market today",
    "NSE quarterly results",
    "stocks to watch moneycontrol",
    "RBI rate decision",
]

HTTP_TIMEOUT = 20.0
MAX_STOCK_SYMBOLS_TO_LLM = 40
MAX_HEADLINES_PER_SYMBOL = 5
MAX_MACRO_HEADLINES = 25
