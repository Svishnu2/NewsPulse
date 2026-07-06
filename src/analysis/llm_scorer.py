"""The single batched Gemini call of the morning: global cues + matched
headlines in, strict-JSON candidate list out."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from src.analysis.entity_match import EventRule, Universe
from src.common import config
from src.fetchers.rss_news import Headline

CATALYST_TYPES = [
    "earnings",
    "order_win",
    "regulatory",
    "macro_sector",
    "rating_action",
    "rumor_media",
    "corporate_action",
    "other",
]

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "market_context": {
            "type": "object",
            "properties": {
                "bias": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                "reasons": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["bias", "reasons"],
        },
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
                    "confidence": {"type": "integer"},
                    "catalyst_type": {"type": "string", "enum": CATALYST_TYPES},
                    "priced_in": {"type": "boolean"},
                    "expected_behavior": {
                        "type": "string",
                        "enum": ["follow_through", "fade_risk"],
                    },
                    "rationale": {"type": "string"},
                    "source_headline": {"type": "string"},
                },
                "required": [
                    "symbol",
                    "direction",
                    "confidence",
                    "catalyst_type",
                    "priced_in",
                    "expected_behavior",
                    "rationale",
                    "source_headline",
                ],
            },
        },
    },
    "required": ["market_context", "candidates"],
}


def build_prompt(
    today: date,
    cue_lines: list[str],
    stock_hits: dict[str, list[Headline]],
    macro_hits: list[tuple[Headline, EventRule]],
    universe: Universe,
) -> str:
    parts: list[str] = [
        "You are an experienced Indian equity pre-market analyst.",
        f"Today is {today.isoformat()}; NSE opens at 09:15 IST.",
        "From overnight news and global cues, propose day-trading-horizon candidates",
        "from the Nifty 200 with LONG or SHORT bias. This is paper research only.",
        "",
        "GLOBAL CUES:",
        *(f"- {line}" for line in cue_lines),
        "",
        "STOCK-SPECIFIC HEADLINES (since previous close):",
    ]

    ranked = sorted(stock_hits.items(), key=lambda kv: len(kv[1]), reverse=True)
    for symbol, headlines in ranked[: config.MAX_STOCK_SYMBOLS_TO_LLM]:
        stock = universe.by_symbol.get(symbol)
        sector = stock.sector if stock else "?"
        for h in headlines[: config.MAX_HEADLINES_PER_SYMBOL]:
            when = h.published_ist[:16] if h.published_ist else "time n/a"
            parts.append(f"- [{symbol} | {sector}] {h.title} ({h.source}, {when})")

    parts += ["", "MACRO HEADLINES (mapped to market events):"]
    seen_sectors: set[str] = set()
    for h, rule in macro_hits[: config.MAX_MACRO_HEADLINES]:
        pos = "|".join(rule.positive_sectors) or "-"
        neg = "|".join(rule.negative_sectors) or "-"
        parts.append(
            f"- {h.title} ({h.source}) [event: {rule.event} | helps: {pos} | hurts: {neg}]"
        )
        seen_sectors.update(rule.positive_sectors)
        seen_sectors.update(rule.negative_sectors)

    if seen_sectors:
        parts += ["", "SECTOR LEADERS you may pick for macro events:"]
        for sector in sorted(seen_sectors):
            members = universe.sector_members(sector)
            if members:
                parts.append(f"- {sector}: {', '.join(members)}")

    parts += [
        "",
        "TASK: return strict JSON matching the schema. Pick up to 12 candidates.",
        "Rules:",
        "- Only use symbols that appear above (stock-specific or sector leaders).",
        "- Penalize stale or already-priced-in news (mark priced_in=true, cut confidence).",
        "- Penalize rumor-grade or unsourced media stories (catalyst_type=rumor_media).",
        "- Confidence 0-100, calibrated: 85+ only for large, fresh, unambiguous catalysts.",
        "- expected_behavior: follow_through if the move should extend intraday,",
        "  fade_risk if the open is likely to retrace.",
        "- rationale: max 25 words. source_headline: quote the headline used.",
        "- At most 2 candidates per sector. Direction must fit catalyst AND market bias.",
    ]
    return "\n".join(parts)


def _call_gemini(prompt: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )
    return response.text or ""


async def score_with_gemini(prompt: str, logger: logging.Logger) -> dict[str, Any] | None:
    """One batched call, up to 3 attempts. None on total failure."""
    if not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set; skipping LLM scoring")
        return None
    for attempt in range(1, 4):
        try:
            raw = await asyncio.to_thread(_call_gemini, prompt)
            data = json.loads(raw)
            if isinstance(data, dict) and "candidates" in data and "market_context" in data:
                logger.info(
                    "Gemini returned %d candidates (attempt %d)",
                    len(data["candidates"]),
                    attempt,
                )
                return data
            logger.warning("Gemini attempt %d: malformed structure", attempt)
        except Exception as exc:
            logger.warning("Gemini attempt %d failed: %s", attempt, exc)
        if attempt < 3:
            await asyncio.sleep(10 * attempt)
    return None


def validate_candidates(
    data: dict[str, Any], universe: Universe, logger: logging.Logger
) -> list[dict[str, Any]]:
    """Drop unknown symbols / bad fields; dedupe by symbol keeping max confidence."""
    best: dict[str, dict[str, Any]] = {}
    for raw in data.get("candidates", []):
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol", "")).strip().upper()
        stock = universe.by_symbol.get(symbol)
        if stock is None:
            logger.info("Dropping unknown symbol from LLM: %r", symbol)
            continue
        direction = str(raw.get("direction", "")).upper()
        if direction not in ("LONG", "SHORT"):
            continue
        try:
            confidence = max(0, min(100, int(raw.get("confidence", 0))))
        except (TypeError, ValueError):
            continue
        catalyst = str(raw.get("catalyst_type", "other"))
        candidate = {
            "symbol": symbol,
            "sector": stock.sector,
            "direction": direction,
            "confidence": confidence,
            "catalyst_type": catalyst if catalyst in CATALYST_TYPES else "other",
            "priced_in": bool(raw.get("priced_in", False)),
            "expected_behavior": (
                raw.get("expected_behavior")
                if raw.get("expected_behavior") in ("follow_through", "fade_risk")
                else "follow_through"
            ),
            "rationale": str(raw.get("rationale", ""))[:200],
            "source_headline": str(raw.get("source_headline", ""))[:200],
        }
        if symbol not in best or confidence > best[symbol]["confidence"]:
            best[symbol] = candidate
    return sorted(best.values(), key=lambda c: c["confidence"], reverse=True)
