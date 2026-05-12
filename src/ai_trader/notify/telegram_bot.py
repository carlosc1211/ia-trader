from __future__ import annotations
import httpx
from ai_trader.config import env


def send_message(text: str, parse_mode: str = "HTML") -> dict:
    token = env("TELEGRAM_BOT_TOKEN", required=True)
    chat_id = env("TELEGRAM_CHAT_ID", required=True)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    r = httpx.post(url, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()
