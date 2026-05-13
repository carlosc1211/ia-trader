"""Smoke test fase 4: ciclo completo de paper trading con señales sintéticas.

Simula 3 ticks:
1. Señal long a 80000 → entry
2. Precio sube a 82000 → take profit
3. Señal flat → no hace nada (verifica risk manager con estado limpio)

No usa Claude para evitar gastar tokens; la señal se construye a mano.
Uso:
    python scripts/smoke_paper.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.brain.risk_validator import validate
from ai_trader.brain.signal import TradingSignal
from ai_trader.config import load_yaml
from ai_trader.execution import paper_broker, risk_manager
from ai_trader.notify.messages import equity_msg, fill_msg, risk_block_msg, signal_msg
from ai_trader.storage.db import get_session, init_db
from ai_trader.storage.repository import save_signal


def tick(session, *, cfg, symbol, signal: TradingSignal, current_price: float, cash: float) -> float:
    print(f"\n=========  TICK · precio ${current_price:,.2f}  cash ${cash:,.2f}  =========")

    # 1) Cerrar salidas si toca SL/TP del estado previo.
    exits = paper_broker.check_exits(
        session, symbol=symbol, current_price=current_price, exec_cfg=cfg["execution"], mode="paper",
    )
    for f in exits:
        kind = "exit_tp" if current_price >= f.position.take_profit else "exit_sl"
        print(fill_msg(symbol, f, kind))
        cash += (f.position.qty * f.order_price) - f.fee_paid

    # 2) Validar la señal y guardarla.
    check = validate(signal, cfg["risk"])
    print(signal_msg(symbol, signal, check))
    row = save_signal(
        session, symbol=symbol, timeframe="4h", signal=signal, check=check,
        model="synthetic", usage=None, snapshot=None,
    )

    if not check.ok:
        return cash

    # 3) Risk manager global.
    rm = risk_manager.check(session, mode="paper", risk_cfg=cfg["risk"])
    if not rm.allow:
        print(risk_block_msg(rm.reasons))
        return cash

    # 4) Entry si aplica.
    fill = paper_broker.execute_entry(
        session, signal=signal, signal_id=row.id, symbol=symbol,
        current_price=current_price, cash_available=cash, exec_cfg=cfg["execution"], mode="paper",
    )
    if fill:
        print(fill_msg(symbol, fill, "entry"))
        cash -= (fill.qty * fill.order_price) + fill.fee_paid

    # 5) Snapshot de equity.
    paper_broker.snapshot_equity(session, mode="paper", cash=cash, current_price=current_price, symbol=symbol)
    eq, unr = paper_broker.compute_equity(session, mode="paper", current_price=current_price, symbol=symbol, cash=cash)
    print(equity_msg("paper", eq, cash, unr))
    return cash


def main() -> None:
    cfg = load_yaml("config.yaml")
    init_db()
    symbol = "BTC/USDT"
    cash = float(cfg["execution"]["initial_capital_usdt"])

    long_sig = TradingSignal(
        action="long", entry=80000, stop_loss=79000, take_profit=82000,
        size_pct=0.1, confidence=0.7,
        rationale="Sintética: cierre 4h sobre EMA20 con volumen >1.5x y MACD cruzando al alza.",
    )
    flat_sig = TradingSignal(
        action="flat", entry=82000, stop_loss=82000, take_profit=82000,
        size_pct=0.0, confidence=0.4,
        rationale="Sintética: tras TP, sin oportunidad clara, esperamos confirmación.",
    )

    with get_session() as s:
        cash = tick(s, cfg=cfg, symbol=symbol, signal=long_sig, current_price=80000, cash=cash)
        cash = tick(s, cfg=cfg, symbol=symbol, signal=flat_sig, current_price=82000, cash=cash)
        cash = tick(s, cfg=cfg, symbol=symbol, signal=flat_sig, current_price=82500, cash=cash)


if __name__ == "__main__":
    main()
