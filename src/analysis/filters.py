"""Deterministic post-LLM filters: surveillance lists, confidence floor,
liquidity floor, sector cap, top-N."""
from __future__ import annotations

import logging
from typing import Any

from src.common import config


def apply_filters(
    candidates: list[dict[str, Any]],
    banned: list[str] | None,
    asm: list[str] | None,
    gsm: list[str] | None,
    turnover_cr: dict[str, float | None],
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    blocked: dict[str, str] = {}
    for symbol in banned or []:
        blocked[symbol] = "F&O ban"
    for symbol in asm or []:
        blocked.setdefault(symbol, "ASM list")
    for symbol in gsm or []:
        blocked.setdefault(symbol, "GSM list")
    if banned is None:
        logger.warning("F&O ban list unavailable — filter skipped")
    if asm is None:
        logger.warning("ASM list unavailable — filter skipped")
    if gsm is None:
        logger.warning("GSM list unavailable — filter skipped")

    kept: list[dict[str, Any]] = []
    for cand in candidates:
        symbol = cand["symbol"]
        if symbol in blocked:
            logger.info("Drop %s: on %s", symbol, blocked[symbol])
            continue
        if cand["confidence"] < config.MIN_CONFIDENCE:
            logger.info("Drop %s: confidence %d < %d", symbol, cand["confidence"],
                        config.MIN_CONFIDENCE)
            continue
        turnover = turnover_cr.get(symbol)
        if turnover is None:
            logger.warning("Keep %s: turnover unknown (yfinance gave no data)", symbol)
        elif turnover < config.MIN_TURNOVER_CRORE:
            logger.info("Drop %s: 20d turnover Rs %.1f cr < %.0f cr", symbol, turnover,
                        config.MIN_TURNOVER_CRORE)
            continue
        cand["avg_turnover_cr"] = turnover
        kept.append(cand)

    # sorted by confidence already (validate_candidates); enforce sector cap + top-N
    per_sector: dict[str, int] = {}
    final: list[dict[str, Any]] = []
    for cand in sorted(kept, key=lambda c: c["confidence"], reverse=True):
        sector = cand.get("sector", "?")
        if per_sector.get(sector, 0) >= config.MAX_PER_SECTOR:
            logger.info("Drop %s: sector cap reached for %s", cand["symbol"], sector)
            continue
        per_sector[sector] = per_sector.get(sector, 0) + 1
        final.append(cand)
        if len(final) >= config.MAX_PICKS:
            break

    if len(final) < 4:
        logger.warning("Only %d picks survived filtering (target 4-6)", len(final))
    return final
