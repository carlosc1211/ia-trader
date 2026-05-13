"""Orquestador de un ciclo de decisión.

Un ciclo:
1. Snapshot multi-timeframe del símbolo.
2. Check de salidas (SL/TP) sobre posiciones abiertas.
3. Decisión Claude.
4. Validación señal (RR, tamaño) + risk manager global.
5. Ejecución paper si todo OK.
6. Snapshot de equity.
7. Notificación Telegram (si hay credenciales).

El estado (cash) se reconstruye desde la última equity_snapshot. Si no hay,
arranca con initial_capital_usdt del config.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_trader.brain.claude_engine import decide
from ai_trader.brain.risk_validator import validate
from ai_trader.config import MODE, env
from ai_trader.data.market_snapshot import build_market_snapshot
from ai_trader.execution import paper_broker, risk_manager
from ai_trader.notify.messages import equity_msg, fill_msg, risk_block_msg, signal_msg
from ai_trader.storage.db import get_session, init_db
from ai_trader.storage.models import EquitySnapshot
from ai_trader.storage.repository import save_signal

log = logging.getLogger("ai_trader.scheduler")


@dataclass
class CycleResult:
    symbol: str
    action: str
    validated: bool
    executed: bool
    notes: list[str]
    equity: float
    cash: float


def _bootstrap_cash(session: Session, *, mode: str, initial: float) -> float:
    stmt = select(EquitySnapshot).where(EquitySnapshot.mode == mode).order_by(
        EquitySnapshot.ts.desc()
    ).limit(1)
    last = session.scalars(stmt).first()
    return float(last.cash) if last else float(initial)


def _maybe_telegram(text: str) -> None:
    if not env("TELEGRAM_BOT_TOKEN") or not env("TELEGRAM_CHAT_ID"):
        return
    try:
        from ai_trader.notify.telegram_bot import send_message
        send_message(text)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def run_cycle(cfg: dict, *, symbol: str | None = None, mode: str | None = None) -> CycleResult:
    init_db()
    mode = mode or MODE
    watch = cfg["watchlist"][0]
    symbol = symbol or watch["symbol"]
    timeframes = watch["timeframes"]
    lookback = watch.get("candles_lookback", 200)

    log.info(f"cycle start · symbol={symbol} · timeframes={timeframes} · mode={mode}")

    snap = build_market_snapshot(symbol, timeframes, lookback=lookback)
    current_price = snap.price
    log.info(f"snapshot · price=${current_price:,.2f}")

    notes: list[str] = []
    with get_session() as s:
        cash = _bootstrap_cash(s, mode=mode, initial=cfg["execution"]["initial_capital_usdt"])
        log.info(f"cash bootstrap · ${cash:,.2f}")

        # 1) Salidas SL/TP primero, con el precio actual.
        exits = paper_broker.check_exits(
            s, symbol=symbol, current_price=current_price,
            exec_cfg=cfg["execution"], mode=mode,
        )
        for f in exits:
            kind = "exit_tp" if current_price >= f.position.take_profit else "exit_sl"
            cash += (f.position.qty * f.order_price) - f.fee_paid
            log.info(f"exit · {kind} · pnl=${f.position.realized_pnl:+.2f}")
            _maybe_telegram(fill_msg(symbol, f, kind))

        # 2) Decisión Claude.
        signal, meta = decide(
            snap.to_dict(),
            model=cfg["llm"]["model"],
            max_tokens=cfg["llm"]["max_tokens"],
            temperature=cfg["llm"]["temperature"],
        )
        log.info(f"signal · {signal.action} · entry=${signal.entry:,.2f} "
                 f"sl=${signal.stop_loss:,.2f} tp=${signal.take_profit:,.2f} "
                 f"rr={signal.risk_reward:.2f} conf={signal.confidence:.2f}")

        check = validate(signal, cfg["risk"])
        row = save_signal(
            s, symbol=symbol, timeframe=timeframes[0], signal=signal, check=check,
            model=meta.get("model"), usage=meta.get("usage"), snapshot=snap.to_dict(),
        )
        _maybe_telegram(signal_msg(symbol, signal, check))

        executed = False
        if not check.ok:
            notes.append("signal rejected by risk_validator: " + "; ".join(check.reasons))
            log.warning(notes[-1])
        else:
            rm = risk_manager.check(s, mode=mode, risk_cfg=cfg["risk"])
            if not rm.allow:
                notes.append("execution blocked by risk_manager: " + "; ".join(rm.reasons))
                log.warning(notes[-1])
                _maybe_telegram(risk_block_msg(rm.reasons))
            else:
                fill = paper_broker.execute_entry(
                    s, signal=signal, signal_id=row.id, symbol=symbol,
                    current_price=current_price, cash_available=cash,
                    exec_cfg=cfg["execution"], mode=mode,
                )
                if fill:
                    cash -= (fill.qty * fill.order_price) + fill.fee_paid
                    executed = True
                    log.info(f"entry · qty={fill.qty:.6f} price=${fill.order_price:,.2f}")
                    _maybe_telegram(fill_msg(symbol, fill, "entry"))

        # 3) Snapshot equity y notificación final.
        paper_broker.snapshot_equity(s, mode=mode, cash=cash, current_price=current_price, symbol=symbol)
        eq, unr = paper_broker.compute_equity(
            s, mode=mode, current_price=current_price, symbol=symbol, cash=cash,
        )
        _maybe_telegram(equity_msg(mode, eq, cash, unr))
        log.info(f"cycle done · equity=${eq:,.2f} cash=${cash:,.2f} unrealized=${unr:+.2f}")

        return CycleResult(
            symbol=symbol, action=signal.action, validated=check.ok,
            executed=executed, notes=notes, equity=eq, cash=cash,
        )
