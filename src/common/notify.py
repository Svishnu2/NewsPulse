"""Optional Telegram notifications. No-ops when secrets are absent."""
from __future__ import annotations

import logging

import httpx

from src.common import config

_MAX_LEN = 4000  # Telegram hard limit is 4096


def telegram_enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


async def send_telegram(text: str, logger: logging.Logger) -> bool:
    if not telegram_enabled():
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text[:_MAX_LEN],
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code == 200:
            logger.info("Telegram message sent (%d chars)", len(text))
            return True
        logger.warning("Telegram send failed: HTTP %d %s", resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        logger.warning("Telegram send error: %s", exc)
    return False
