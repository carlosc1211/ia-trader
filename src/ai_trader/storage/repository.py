"""Operaciones de alto nivel sobre la base de datos."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_trader.brain.risk_validator import RiskCheck
from ai_trader.brain.signal import TradingSignal
from ai_trader.storage.models import EquitySnapshot, Order, Position, Signal


def save_signal(
    session: Session,
    *,
    symbol: str,
    timeframe: str,
    signal: TradingSignal,
    check: RiskCheck,
    model: str | None = None,
    usage: dict | None = None,
    snapshot: dict | None = None,
) -> Signal:
    row = Signal(
        symbol=symbol,
        timeframe=timeframe,
        action=signal.action,
        entry=signal.entry,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        size_pct=signal.size_pct,
        confidence=signal.confidence,
        risk_reward=signal.risk_reward,
        rationale=signal.rationale,
        validated=check.ok,
        rejection_reasons=check.reasons or None,
        model=model,
        usage=usage,
        snapshot=snapshot,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_recent_signals(session: Session, limit: int = 10) -> list[Signal]:
    stmt = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


def open_position(
    session: Session,
    *,
    symbol: str,
    direction: str,
    qty: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    mode: str,
    signal_id: int | None = None,
) -> Position:
    pos = Position(
        symbol=symbol, direction=direction, qty=qty, entry_price=entry_price,
        stop_loss=stop_loss, take_profit=take_profit, mode=mode, signal_id=signal_id,
    )
    session.add(pos)
    session.commit()
    session.refresh(pos)
    return pos


def close_position(session: Session, *, position_id: int, exit_price: float,
                   realized_pnl: float) -> Position:
    pos = session.get(Position, position_id)
    if pos is None:
        raise ValueError(f"Position {position_id} not found")
    pos.closed_at = datetime.now(timezone.utc)
    pos.exit_price = exit_price
    pos.realized_pnl = realized_pnl
    session.commit()
    session.refresh(pos)
    return pos


def open_positions(session: Session, symbol: str | None = None) -> list[Position]:
    stmt = select(Position).where(Position.closed_at.is_(None))
    if symbol:
        stmt = stmt.where(Position.symbol == symbol)
    return list(session.scalars(stmt))


def record_order(
    session: Session,
    *,
    symbol: str,
    side: str,
    kind: str,
    mode: str,
    qty: float,
    price: float,
    fee: float = 0.0,
    pnl: float | None = None,
    signal_id: int | None = None,
    external_id: str | None = None,
) -> Order:
    order = Order(
        symbol=symbol, side=side, kind=kind, mode=mode, qty=qty, price=price,
        fee=fee, pnl=pnl, signal_id=signal_id, external_id=external_id,
    )
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def record_equity(session: Session, *, mode: str, equity: float, cash: float,
                  unrealized_pnl: float = 0.0) -> EquitySnapshot:
    snap = EquitySnapshot(mode=mode, equity=equity, cash=cash, unrealized_pnl=unrealized_pnl)
    session.add(snap)
    session.commit()
    session.refresh(snap)
    return snap
