"""Smoke test fase 7: fuerza una entrada long + OCO en Binance Testnet.

NO usa Claude. Crea una TradingSignal sintética con SL y TP a +/-1.5%
del precio actual y un size pequeño (0.5% del cash, ~$50 notional).
Sirve para validar que el flujo de compra MARKET + OCO funciona.

Uso:
    python scripts/smoke_testnet_entry.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.brain.signal import TradingSignal
from ai_trader.brain.risk_validator import RiskCheck
from ai_trader.config import load_yaml
from ai_trader.data.binance_client import fetch_ticker
from ai_trader.execution import binance_broker
from ai_trader.logging_setup import setup as setup_logging
from ai_trader.notify.messages import fill_msg
from ai_trader.storage.db import get_session, init_db
from ai_trader.storage.repository import save_signal


def main() -> None:
    setup_logging()
    cfg = load_yaml("config.yaml")
    symbol = cfg["watchlist"][0]["symbol"]
    mode = "testnet"

    ex = binance_broker.make_authenticated_exchange(mode=mode)
    init_db()

    price = float(fetch_ticker(symbol)["last"])
    print(f"Precio actual {symbol}: ${price:,.2f}")

    # Síntesis: SL/TP a +/-1.5%, size 0.5% para notional ~$50.
    signal = TradingSignal(
        action="long",
        entry=price,
        stop_loss=round(price * 0.985, 2),
        take_profit=round(price * 1.015, 2),
        size_pct=0.005,
        confidence=0.90,
        rationale="Smoke testnet: señal sintética para validar flujo MARKET + OCO. NO REAL.",
    )
    print(f"Signal sintética: entry={signal.entry} SL={signal.stop_loss} TP={signal.take_profit} size_pct={signal.size_pct}")

    with get_session() as s:
        state = binance_broker.sync_state(s, ex, symbol=symbol, mode=mode)
        print(f"\nSync · cash USDT={state.cash_usdt:.2f}  base BTC={state.base_qty:.6f}")
        print(f"      open_oco={len(state.open_oco_orders)}  db_open={state.db_open_positions}\n")

        row = save_signal(
            s, symbol=symbol, timeframe="4h", signal=signal,
            check=RiskCheck(ok=True, reasons=[]),
            model="synthetic-smoke", usage=None, snapshot=None,
        )
        print(f"Signal guardada id={row.id}")

        fill = binance_broker.execute_entry(
            s, signal=signal, signal_id=row.id, symbol=symbol,
            cash_available=state.cash_usdt, exec_cfg=cfg["execution"], mode=mode, ex=ex,
        )
        if fill is None:
            print("execute_entry devolvió None — abortado por el broker (mira logs).")
            return

        print("\n── FILL ──")
        print(fill_msg(symbol, fill, "entry").replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>",""))
        print(f"\nPosition id={fill.position.id} qty={fill.qty:.8f} entry=${fill.order_price:,.2f}")
        print(f"\nVe a https://testnet.binance.vision para verificar la OCO en el dashboard.")


if __name__ == "__main__":
    main()
