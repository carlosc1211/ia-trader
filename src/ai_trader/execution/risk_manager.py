"""Risk manager global: aplica límites operativos antes de ejecutar.

A diferencia de brain.risk_validator (que valida una señal en aislamiento),
aquí miramos el estado actual de la cuenta: posiciones concurrentes,
pérdida diaria realizada, drawdown desde el peak histórico.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_trader.storage.models import EquitySnapshot, Order, Position


@dataclass
class ExecutionDecision:
    allow: bool
    reasons: list[str]


def _today_utc_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _realized_pnl_today(session: Session, mode: str) -> float:
    stmt = select(func.coalesce(func.sum(Order.pnl), 0.0)).where(
        Order.created_at >= _today_utc_start(),
        Order.mode == mode,
        Order.pnl.is_not(None),
    )
    return float(session.execute(stmt).scalar_one())


def _peak_equity(session: Session, mode: str) -> float | None:
    stmt = select(func.max(EquitySnapshot.equity)).where(EquitySnapshot.mode == mode)
    val = session.execute(stmt).scalar_one()
    return float(val) if val is not None else None


def _current_equity(session: Session, mode: str) -> float | None:
    stmt = select(EquitySnapshot).where(EquitySnapshot.mode == mode).order_by(
        EquitySnapshot.ts.desc()
    ).limit(1)
    snap = session.scalars(stmt).first()
    return float(snap.equity) if snap else None


def _open_positions_count(session: Session, mode: str) -> int:
    stmt = select(func.count(Position.id)).where(
        Position.closed_at.is_(None), Position.mode == mode
    )
    return int(session.execute(stmt).scalar_one())


def check(session: Session, *, mode: str, risk_cfg: dict) -> ExecutionDecision:
    reasons: list[str] = []

    max_concurrent = int(risk_cfg.get("max_concurrent_positions", 2))
    n_open = _open_positions_count(session, mode)
    if n_open >= max_concurrent:
        reasons.append(f"max_concurrent_positions alcanzado ({n_open}/{max_concurrent})")

    equity = _current_equity(session, mode)
    if equity and equity > 0:
        pnl_today = _realized_pnl_today(session, mode)
        max_daily_loss = float(risk_cfg.get("max_daily_loss_pct", 0.05))
        if pnl_today < 0 and abs(pnl_today) / equity >= max_daily_loss:
            reasons.append(
                f"max_daily_loss alcanzado ({pnl_today:.2f} = "
                f"{abs(pnl_today)/equity*100:.2f}% de {equity:.2f})"
            )

        peak = _peak_equity(session, mode)
        if peak and peak > 0:
            dd = (peak - equity) / peak
            max_dd = float(risk_cfg.get("max_drawdown_pct", 0.15))
            if dd >= max_dd:
                reasons.append(f"max_drawdown alcanzado ({dd*100:.2f}% desde peak {peak:.2f})")

    return ExecutionDecision(allow=not reasons, reasons=reasons)
