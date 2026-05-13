"""Broker simulado para paper trading.

Aplica slippage y fees del config a un precio "de mercado" (el precio
actual del snapshot). Mantiene la posición en DB y registra cada fill
como Order. La equity se recalcula al abrir/cerrar y se guarda como
EquitySnapshot.

Soporta solo spot long (compra a entry, venta a SL/TP) en esta fase.
Short queda para más adelante cuando integremos derivados.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ai_trader.brain.signal import TradingSignal
from ai_trader.storage.models import Position
from ai_trader.storage.repository import (
    close_position, open_position, open_positions, record_equity, record_order,
)


@dataclass
class FillResult:
    position: Position
    order_price: float
    fee_paid: float
    qty: float


def _apply_slippage(price: float, side: str, slippage_pct: float) -> float:
    if side == "buy":
        return price * (1 + slippage_pct)
    return price * (1 - slippage_pct)


def execute_entry(
    session: Session,
    *,
    signal: TradingSignal,
    signal_id: int,
    symbol: str,
    current_price: float,
    cash_available: float,
    exec_cfg: dict,
    mode: str = "paper",
) -> FillResult | None:
    """Abre posición long en paper. Devuelve None si size_pct=0 o no hay cash."""
    if signal.action != "long" or signal.size_pct <= 0:
        return None

    slippage = float(exec_cfg.get("slippage_pct", 0.0005))
    fee_pct = float(exec_cfg.get("fee_pct", 0.001))

    notional = cash_available * signal.size_pct
    if notional <= 0:
        return None

    fill_price = _apply_slippage(current_price, "buy", slippage)
    qty = notional / fill_price
    fee = notional * fee_pct

    pos = open_position(
        session,
        symbol=symbol, direction="long", qty=qty,
        entry_price=fill_price, stop_loss=signal.stop_loss,
        take_profit=signal.take_profit, mode=mode, signal_id=signal_id,
    )
    record_order(
        session, symbol=symbol, side="buy", kind="entry", mode=mode,
        qty=qty, price=fill_price, fee=fee, signal_id=signal_id,
    )
    return FillResult(position=pos, order_price=fill_price, fee_paid=fee, qty=qty)


def _exit_position(
    session: Session, *, position: Position, exit_price: float, kind: str,
    exec_cfg: dict, mode: str,
) -> FillResult:
    slippage = float(exec_cfg.get("slippage_pct", 0.0005))
    fee_pct = float(exec_cfg.get("fee_pct", 0.001))

    fill_price = _apply_slippage(exit_price, "sell", slippage)
    gross = position.qty * (fill_price - position.entry_price)
    fee = position.qty * fill_price * fee_pct
    entry_fee = position.qty * position.entry_price * fee_pct
    pnl = gross - fee - entry_fee

    close_position(session, position_id=position.id, exit_price=fill_price, realized_pnl=pnl)
    record_order(
        session, symbol=position.symbol, side="sell", kind=kind, mode=mode,
        qty=position.qty, price=fill_price, fee=fee, pnl=pnl,
        signal_id=position.signal_id,
    )
    return FillResult(position=position, order_price=fill_price, fee_paid=fee, qty=position.qty)


def check_exits(
    session: Session, *, symbol: str, current_price: float, exec_cfg: dict,
    mode: str = "paper",
) -> list[FillResult]:
    """Cierra posiciones long si el precio actual toca SL o TP."""
    fills: list[FillResult] = []
    for pos in open_positions(session, symbol):
        if pos.direction != "long":
            continue
        if current_price <= pos.stop_loss:
            fills.append(_exit_position(
                session, position=pos, exit_price=pos.stop_loss,
                kind="exit_sl", exec_cfg=exec_cfg, mode=mode,
            ))
        elif current_price >= pos.take_profit:
            fills.append(_exit_position(
                session, position=pos, exit_price=pos.take_profit,
                kind="exit_tp", exec_cfg=exec_cfg, mode=mode,
            ))
    return fills


def compute_equity(session: Session, *, mode: str, current_price: float,
                    symbol: str, cash: float) -> tuple[float, float]:
    """Devuelve (equity_total, unrealized_pnl) marcando a mercado las posiciones abiertas.

    equity = cash + valor de mercado de las posiciones abiertas.
    unrealized = valor de mercado - cost_basis (qty * entry_price).
    """
    position_value = 0.0
    unrealized = 0.0
    for pos in open_positions(session, symbol):
        if pos.direction == "long":
            mv = pos.qty * current_price
            position_value += mv
            unrealized += mv - pos.qty * pos.entry_price
    return cash + position_value, unrealized


def snapshot_equity(session: Session, *, mode: str, cash: float, current_price: float,
                     symbol: str) -> None:
    equity, unrealized = compute_equity(
        session, mode=mode, current_price=current_price, symbol=symbol, cash=cash,
    )
    record_equity(session, mode=mode, equity=equity, cash=cash, unrealized_pnl=unrealized)
