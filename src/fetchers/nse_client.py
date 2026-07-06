"""NSE API client. NSE blocks naive clients, so we warm up cookies on the
homepage with browser-like headers and retry with backoff. Every method
degrades to None on persistent failure — callers log and continue."""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import date
from typing import Any, Self

import httpx

from src.fetchers.rss_news import Headline

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
_BASE = "https://www.nseindia.com"
_BAN_CSV_FALLBACK = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
_RETRIES = 3


class NSEClient:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=25.0, follow_redirects=True)
        await self._warmup()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    async def _warmup(self) -> None:
        assert self._client is not None
        try:
            await self._client.get(_BASE)  # sets the cookies the /api endpoints require
        except httpx.HTTPError as exc:
            self._logger.warning("NSE warmup failed: %s", exc)

    async def _get_json(self, path: str, params: dict[str, str] | None = None) -> Any | None:
        assert self._client is not None
        for attempt in range(1, _RETRIES + 1):
            try:
                resp = await self._client.get(f"{_BASE}{path}", params=params)
                if resp.status_code in (401, 403):
                    await self._warmup()
                    raise httpx.HTTPStatusError(
                        "blocked", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                self._logger.warning("NSE %s attempt %d/%d: %s", path, attempt, _RETRIES, exc)
                if attempt < _RETRIES:
                    await asyncio.sleep(2**attempt)
        self._logger.warning("NSE %s: giving up, continuing without this source", path)
        return None

    @staticmethod
    def _symbols_in(payload: Any) -> list[str]:
        """Recursively collect every 'symbol' value in an arbitrary NSE payload."""
        found: list[str] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key.lower() == "symbol" and isinstance(value, str):
                    found.append(value.strip().upper())
                else:
                    found.extend(NSEClient._symbols_in(value))
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, str):
                    found.append(item.strip().upper())
                else:
                    found.extend(NSEClient._symbols_in(item))
        return found

    async def corporate_announcements(self, frm: date, to: date) -> list[Headline] | None:
        params = {
            "index": "equities",
            "from_date": frm.strftime("%d-%m-%Y"),
            "to_date": to.strftime("%d-%m-%Y"),
        }
        payload = await self._get_json("/api/corporate-announcements", params)
        if payload is None:
            return None
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        items: list[Headline] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).strip().upper()
            desc = str(row.get("desc") or row.get("subject") or "").strip()
            detail = str(row.get("attchmntText") or row.get("sm_name") or "").strip()
            when = str(row.get("an_dt") or row.get("sort_date") or "").strip()
            if not symbol or not desc:
                continue
            items.append(
                Headline(
                    title=f"{symbol}: {desc}"[:200],
                    summary=detail[:300],
                    source="NSE Announcements",
                    published_ist=when,
                )
            )
        self._logger.info("NSE announcements: %d items", len(items))
        return items

    async def fno_ban_list(self) -> list[str] | None:
        payload = await self._get_json("/api/foSecurityInBanPeriod")
        if payload is not None:
            symbols = self._symbols_in(payload)
            self._logger.info("F&O ban list: %d symbols", len(symbols))
            return symbols
        # Fallback: public archive CSV (no cookie dance needed)
        try:
            assert self._client is not None
            resp = await self._client.get(_BAN_CSV_FALLBACK)
            resp.raise_for_status()
            reader = csv.reader(io.StringIO(resp.text))
            symbols = [
                row[1].strip().upper()
                for row in reader
                if len(row) > 1 and row[1].strip().upper() not in ("SYMBOL", "")
            ]
            self._logger.info("F&O ban list (archive CSV): %d symbols", len(symbols))
            return symbols
        except Exception as exc:
            self._logger.warning("F&O ban fallback failed: %s", exc)
            return None

    async def asm_list(self) -> list[str] | None:
        payload = await self._get_json("/api/reportASM")
        return None if payload is None else self._symbols_in(payload)

    async def gsm_list(self) -> list[str] | None:
        payload = await self._get_json("/api/reportGSM")
        return None if payload is None else self._symbols_in(payload)
