"""08:00 IST pre-market job: cues + news -> one Gemini call -> filtered picks.

Never lets an exception escape (the workflow must stay green); always tries
to commit its run log. Idempotent: re-runs regenerate today's file but keep
any gap_check/verification blocks already recorded for surviving symbols.
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import date, time
from typing import Any

from src.analysis.entity_match import EventMap, Universe, match_news
from src.analysis.filters import apply_filters
from src.analysis.llm_scorer import build_prompt, score_with_gemini, validate_candidates
from src.common import config
from src.common.logging_setup import get_logger
from src.common.notify import send_telegram, telegram_enabled
from src.common.storage import commit_and_push, read_json, write_json
from src.common.timeutils import ist_iso, is_trading_day, news_cutoff, now_ist, today_ist
from src.fetchers.global_cues import cues_summary_lines, fetch_global_cues
from src.fetchers.nse_client import NSEClient
from src.fetchers.prices import turnover_crore
from src.fetchers.rss_news import Headline, dedupe, fetch_all_news

logger = get_logger("premarket")


def _day_file(day: date):
    return config.PREDICTIONS_DIR / f"{day.isoformat()}.json"


async def _nse_bundle(
    frm: date, to: date
) -> tuple[list[Headline] | None, list[str] | None, list[str] | None, list[str] | None]:
    try:
        async with NSEClient(logger) as nse:
            announcements = await nse.corporate_announcements(frm, to)
            ban = await nse.fno_ban_list()
            asm = await nse.asm_list()
            gsm = await nse.gsm_list()
        return announcements, ban, asm, gsm
    except Exception as exc:
        logger.warning("NSE bundle failed entirely: %s", exc)
        return None, None, None, None


def _merge_previous(day_file, predictions: list[dict[str, Any]]) -> None:
    """Keep gap_check/verification from an earlier run of the same day."""
    existing = read_json(day_file)
    if not existing:
        return
    old = {p["symbol"]: p for p in existing.get("predictions", [])}
    for pred in predictions:
        prior = old.get(pred["symbol"])
        if prior:
            for key in ("gap_check", "verification"):
                if key in prior:
                    pred[key] = prior[key]


def _telegram_text(day: date, bias: str, picks: list[dict[str, Any]]) -> str:
    lines = [f"NewsPulse {day.isoformat()} — market bias: {bias}"]
    for i, p in enumerate(picks, 1):
        stance = "long bias" if p["direction"] == "LONG" else "short bias"
        lines.append(
            f"{i}. {p['symbol']} {stance} {p['confidence']}% "
            f"[{p['catalyst_type']}] {p['rationale']}"
        )
    if not picks:
        lines.append("No picks passed the filters today.")
    lines.append("Educational research tool. Not investment advice.")
    return "\n".join(lines)


async def run() -> int:
    today = today_ist()
    started = now_ist()
    logger.info("Pre-market job starting at %s", ist_iso(started))

    if not is_trading_day(today) and not config.FORCE_RUN:
        logger.info("%s is a weekend/NSE holiday — exiting quietly", today)
        return 0

    late_cutoff = time(*config.LATE_START_CUTOFF)
    if started.time() > late_cutoff and not config.FORCE_RUN:
        logger.warning(
            "ABORT: started %s IST, after the %s cutoff (GitHub cron lateness guard). "
            "Predictions made this close to the open would be unusable.",
            started.time().strftime("%H:%M"),
            late_cutoff.strftime("%H:%M"),
        )
        commit_and_push(f"premarket {today}: aborted (late start)", logger)
        return 0

    cutoff = news_cutoff(today)
    logger.info("News window: since %s", ist_iso(cutoff))

    cues, rss_items, nse_bundle = await asyncio.gather(
        fetch_global_cues(logger),
        fetch_all_news(cutoff, logger),
        _nse_bundle(cutoff.date(), today),
    )
    announcements, ban, asm, gsm = nse_bundle
    headlines = dedupe(rss_items + (announcements or []))
    logger.info("Total headlines: %d (RSS %d, NSE %d)", len(headlines), len(rss_items),
                len(announcements or []))

    universe = Universe.load()
    event_map = EventMap.load()
    stock_hits, macro_hits = match_news(headlines, universe, event_map)
    logger.info("Matched: %d symbols with direct news, %d macro hits",
                len(stock_hits), len(macro_hits))

    payload: dict[str, Any] = {
        "date": today.isoformat(),
        "generated_at": ist_iso(),
        "status": "pending",
        "market_context": {
            "bias": cues.get("derived_bias", "neutral"),
            "reasons": [],
            "cues": {k: v for k, v in cues.items() if isinstance(v, dict)},
        },
        "predictions": [],
    }

    if not stock_hits and not macro_hits:
        payload["note"] = "No matched news this morning."
        logger.warning("No matched headlines — publishing empty prediction file")
    else:
        prompt = build_prompt(today, cues_summary_lines(cues), stock_hits, macro_hits, universe)
        logger.info("Prompt size: %d chars", len(prompt))
        result = await score_with_gemini(prompt, logger)
        if result is None:
            payload["note"] = "LLM scoring failed after retries."
            logger.error("Gemini scoring failed — publishing empty prediction file")
        else:
            context = result.get("market_context", {})
            if context.get("bias") in ("bullish", "bearish", "neutral"):
                payload["market_context"]["bias"] = context["bias"]
            payload["market_context"]["reasons"] = [
                str(r)[:200] for r in context.get("reasons", [])[:6]
            ]
            candidates = validate_candidates(result, universe, logger)

            turnovers = await asyncio.gather(
                *(
                    turnover_crore(universe.by_symbol[c["symbol"]].yahoo, logger)
                    for c in candidates
                )
            )
            turnover_map = {c["symbol"]: t for c, t in zip(candidates, turnovers)}
            payload["predictions"] = apply_filters(
                candidates, ban, asm, gsm, turnover_map, logger
            )

    day_file = _day_file(today)
    _merge_previous(day_file, payload["predictions"])
    write_json(day_file, payload)
    logger.info("Wrote %s with %d picks", day_file.name, len(payload["predictions"]))

    commit_and_push(f"premarket {today}: {len(payload['predictions'])} picks", logger)

    if telegram_enabled():
        await send_telegram(
            _telegram_text(today, payload["market_context"]["bias"], payload["predictions"]),
            logger,
        )
    elapsed = (now_ist() - started).total_seconds()
    logger.info("Pre-market job done in %.1fs", elapsed)
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except Exception:
        logger.error("Unhandled error:\n%s", traceback.format_exc())
        commit_and_push(f"premarket {today_ist()}: failed run log", logger)
        code = 0  # never fail the workflow; the log tells the story
    sys.exit(code)


if __name__ == "__main__":
    main()
