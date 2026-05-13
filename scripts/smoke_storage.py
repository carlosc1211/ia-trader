"""Smoke test fase 3: crear tablas, insertar señal sintética, releer.

Uso:
    python scripts/smoke_storage.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.brain.risk_validator import RiskCheck
from ai_trader.brain.signal import TradingSignal
from ai_trader.storage.db import DB_URL, get_session, init_db
from ai_trader.storage.repository import (
    list_recent_signals, open_position, open_positions, record_equity, save_signal,
)


def main() -> None:
    print(f"DB: {DB_URL}")
    init_db()
    print("Tablas creadas/verificadas.")

    sig = TradingSignal(
        action="long",
        entry=80000.0,
        stop_loss=79000.0,
        take_profit=82000.0,
        size_pct=0.1,
        confidence=0.7,
        rationale="Test: cierre 4h sobre EMA20 con volumen >1.5x y RSI 55.",
    )
    check = RiskCheck(ok=True, reasons=[])

    with get_session() as s:
        row = save_signal(
            s, symbol="BTC/USDT", timeframe="4h",
            signal=sig, check=check,
            model="claude-sonnet-4-6", usage={"input_tokens": 3243, "output_tokens": 476},
        )
        print(f"Signal guardada id={row.id} RR={row.risk_reward:.2f}")

        pos = open_position(
            s, symbol="BTC/USDT", direction="long", qty=0.0025,
            entry_price=80000.0, stop_loss=79000.0, take_profit=82000.0,
            mode="paper", signal_id=row.id,
        )
        print(f"Position abierta id={pos.id}")

        record_equity(s, mode="paper", equity=1000.0, cash=800.0, unrealized_pnl=0.0)
        print("Equity snapshot guardado.")

        recent = list_recent_signals(s, limit=5)
        print(f"\nÚltimas {len(recent)} señales:")
        for r in recent:
            print(f"  [{r.created_at:%Y-%m-%d %H:%M}] {r.symbol} {r.action} "
                  f"size={r.size_pct} RR={r.risk_reward:.2f} valid={r.validated}")

        opens = open_positions(s, "BTC/USDT")
        print(f"\nPosiciones abiertas en BTC/USDT: {len(opens)}")


if __name__ == "__main__":
    main()
