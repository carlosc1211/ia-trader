"""Smoke test fase 0: tick de BTC + envío a Telegram.

Uso:
    python scripts/hello.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.data.binance_client import fetch_ticker
from ai_trader.notify.telegram_bot import send_message


def main() -> None:
    t = fetch_ticker("BTC/USDT")
    last = t["last"]
    high = t["high"]
    low = t["low"]
    pct = t.get("percentage")
    msg = (
        "<b>🤖 AI Trader — Hello</b>\n"
        f"BTC/USDT: <b>${last:,.2f}</b>\n"
        f"24h: H ${high:,.2f} · L ${low:,.2f}"
        + (f" · {pct:+.2f}%" if pct is not None else "")
    )
    print(msg.replace("<b>", "").replace("</b>", ""))
    res = send_message(msg)
    print(f"Telegram OK: msg_id={res['result']['message_id']}")


if __name__ == "__main__":
    main()
