"""Concurrent RSS + Google News fetching. Headlines/summaries only —
full articles are never scraped (paywall/robots friendly)."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import quote_plus

import feedparser
import httpx

from src.common import config
from src.common.timeutils import ist_iso, struct_to_ist

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NORM_RE = re.compile(r"[^a-z0-9]+")

GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


@dataclass
class Headline:
    title: str
    summary: str
    source: str
    published_ist: str  # ISO string; "" when the feed omitted a timestamp
    link: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def text(self) -> str:
        return f"{self.title} {self.summary}"


def title_hash(title: str) -> str:
    norm = _NORM_RE.sub("", title.lower())
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def _clean(html: str, limit: int = 300) -> str:
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()
    return text[:limit]


def _parse_entries(raw: bytes, source: str, cutoff: datetime) -> list[Headline]:
    feed = feedparser.parse(raw)
    out: list[Headline] = []
    for entry in feed.entries:
        title = _clean(getattr(entry, "title", ""), 200)
        if not title:
            continue
        st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        published = ""
        if st is not None:
            dt = struct_to_ist(st)
            if dt < cutoff:
                continue  # older than previous trading day 15:30 IST
            published = ist_iso(dt)
        out.append(
            Headline(
                title=title,
                summary=_clean(getattr(entry, "summary", "")),
                source=source,
                published_ist=published,
                link=getattr(entry, "link", "") or "",
            )
        )
    return out


async def _fetch_feed(
    client: httpx.AsyncClient, name: str, url: str, cutoff: datetime, logger: logging.Logger
) -> list[Headline]:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        items = await asyncio.to_thread(_parse_entries, resp.content, name, cutoff)
        logger.info("Feed %-28s -> %d fresh items", name, len(items))
        return items
    except Exception as exc:
        logger.warning("Feed %s failed: %s", name, exc)
        return []


def dedupe(headlines: list[Headline]) -> list[Headline]:
    seen: set[str] = set()
    unique: list[Headline] = []
    for h in headlines:
        key = title_hash(h.title)
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique


async def fetch_all_news(cutoff: datetime, logger: logging.Logger) -> list[Headline]:
    """All enabled RSS feeds + Google News queries, concurrently, deduped."""
    feeds: list[tuple[str, str]] = [
        (str(f["name"]), str(f["url"])) for f in config.RSS_FEEDS if f.get("enabled")
    ]
    feeds += [
        (f"Google News: {q}", GOOGLE_NEWS_URL.format(q=quote_plus(q)))
        for q in config.GOOGLE_NEWS_QUERIES
    ]
    headers = {"User-Agent": _UA, "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8"}
    async with httpx.AsyncClient(
        headers=headers, timeout=config.HTTP_TIMEOUT, follow_redirects=True
    ) as client:
        results = await asyncio.gather(
            *(_fetch_feed(client, name, url, cutoff, logger) for name, url in feeds)
        )
    merged = dedupe([h for batch in results for h in batch])
    logger.info("News total after dedupe: %d headlines", len(merged))
    return merged
