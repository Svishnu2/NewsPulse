"""Map headlines to Nifty 200 symbols (alias table) and to macro events
(event_sector_map.csv keywords -> affected sectors)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field

from src.common import config
from src.fetchers.rss_news import Headline

# Aliases that are ordinary English words: match only as exact uppercase
# tokens so "oil prices" doesn't tag Oil India.
_AMBIGUOUS = {"OIL", "IDEA", "TRENT", "TITAN", "ACC", "PAGE"}


@dataclass(frozen=True)
class Stock:
    symbol: str
    name: str
    aliases: tuple[str, ...]
    sector: str
    yahoo: str


class Universe:
    def __init__(self, stocks: list[Stock]) -> None:
        self.stocks = stocks
        self.by_symbol: dict[str, Stock] = {s.symbol: s for s in stocks}
        self._patterns: list[tuple[re.Pattern[str], str]] = []
        for stock in stocks:
            for alias in {stock.symbol, *stock.aliases}:
                if not alias:
                    continue
                escaped = re.escape(alias)
                if alias.upper() in _AMBIGUOUS and alias.upper() == alias:
                    pattern = re.compile(rf"\b{escaped}\b")  # case-sensitive
                else:
                    pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
                self._patterns.append((pattern, stock.symbol))

    @classmethod
    def load(cls) -> "Universe":
        stocks: list[Stock] = []
        with config.NIFTY200_CSV.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                aliases = tuple(
                    a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()
                )
                stocks.append(
                    Stock(
                        symbol=row["symbol"].strip().upper(),
                        name=row["company_name"].strip(),
                        aliases=aliases,
                        sector=row["sector"].strip(),
                        yahoo=row["yahoo_ticker"].strip(),
                    )
                )
        return cls(stocks)

    def symbols_in(self, text: str) -> set[str]:
        return {symbol for pattern, symbol in self._patterns if pattern.search(text)}

    def sector_members(self, sector: str, limit: int = 8) -> list[str]:
        return [s.symbol for s in self.stocks if s.sector == sector][:limit]


@dataclass(frozen=True)
class EventRule:
    event: str
    keywords: tuple[str, ...]
    positive_sectors: tuple[str, ...]
    negative_sectors: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...] = field(default=(), compare=False)


class EventMap:
    def __init__(self, rules: list[EventRule]) -> None:
        self.rules = rules

    @classmethod
    def load(cls) -> "EventMap":
        rules: list[EventRule] = []
        with config.EVENT_SECTOR_CSV.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                keywords = tuple(
                    k.strip() for k in (row.get("keywords") or "").split("|") if k.strip()
                )
                rules.append(
                    EventRule(
                        event=row["event"].strip(),
                        keywords=keywords,
                        positive_sectors=tuple(
                            s.strip()
                            for s in (row.get("positive_sectors") or "").split("|")
                            if s.strip()
                        ),
                        negative_sectors=tuple(
                            s.strip()
                            for s in (row.get("negative_sectors") or "").split("|")
                            if s.strip()
                        ),
                        patterns=tuple(
                            re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in keywords
                        ),
                    )
                )
        return cls(rules)

    def events_in(self, text: str) -> list[EventRule]:
        return [r for r in self.rules if any(p.search(text) for p in r.patterns)]


def match_news(
    headlines: list[Headline], universe: Universe, event_map: EventMap
) -> tuple[dict[str, list[Headline]], list[tuple[Headline, EventRule]]]:
    """Split news into stock-specific hits (symbol -> headlines) and
    macro hits (headline, matched event rule)."""
    stock_hits: dict[str, list[Headline]] = {}
    macro_hits: list[tuple[Headline, EventRule]] = []
    for headline in headlines:
        text = headline.text
        symbols = universe.symbols_in(text)
        if symbols:
            for symbol in symbols:
                stock_hits.setdefault(symbol, []).append(headline)
            continue  # a stock-specific story shouldn't double as macro
        for rule in event_map.events_in(text):
            macro_hits.append((headline, rule))
    return stock_hits, macro_hits
