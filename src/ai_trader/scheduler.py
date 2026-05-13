"""Orquestador de un ciclo de decisión, soporta paper / testnet / live.

Un ciclo:
1. Procesa comandos pendientes de Telegram (pausa, stop, close_all).
2. Si está en live/testnet, conecta con Binance autenticado y sincroniza estado.
3. Snapshot multi-timeframe del símbolo (datos públicos siempre).
4. Reconcilia salidas (SL/TP) sobre posiciones abiertas.
5. Decisión Claude.
6. Validación señal (RR, tamaño) + risk manager global.
7. Ejecución según modo.
8. Snapshot de equity.
9. Notificación Telegram.

Modos:
  paper   — broker simulado, dinero ficticio del config.
  testnet — Binance testnet real (testnet.binance.vision), dinero ficticio.
  live    — Binance producción. Requiere AI_TRADER_ALLOW_LIVE=yes.
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
from ai_trader.notify import telegram_control
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
    skipped_reason: str | None
    notes: list[str]
    equity: float
    cash: float
    stop_requested: bool


def _bootstrap_cash_paper(session: Session, *, mode: str, initial: float) -> float:
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


def _run_paper(s: Session, *, cfg: dict, symbol: str, timeframes: list[str],
               snap_dict: dict, current_price: float, mode: str) -> CycleResult:
    cash = _bootstrap_cash_paper(s, mode=mode, initial=cfg["execution"]["initial_capital_usdt"])
    log.info(f"cash bootstrap · ${cash:,.2f}")

    exits = paper_broker.check_exits(
        s, symbol=symbol, current_price=current_price, exec_cfg=cfg["execution"], mode=mode,
    )
    for f in exits:
        kind = "exit_tp" if current_price >= f.position.take_profit else "exit_sl"
        cash += (f.position.qty * f.order_price) - f.fee_paid
        log.info(f"exit · {kind} · pnl=${f.position.realized_pnl:+.2f}")
        _maybe_telegram(fill_msg(symbol, f, kind))

    signal, meta = decide(
        snap_dict, model=cfg["llm"]["model"],
        max_tokens=cfg["llm"]["max_tokens"], temperature=cfg["llm"]["temperature"],
    )
    log.info(f"signal · {signal.action} entry=${signal.entry:,.2f} "
             f"sl=${signal.stop_loss:,.2f} tp=${signal.take_profit:,.2f} "
             f"rr={signal.risk_reward:.2f} conf={signal.confidence:.2f}")

    check = validate(signal, cfg["risk"])
    row = save_signal(
        s, symbol=symbol, timeframe=timeframes[0], signal=signal, check=check,
        model=meta.get("model"), usage=meta.get("usage"), snapshot=snap_dict,
    )
    _maybe_telegram(signal_msg(symbol, signal, check))

    executed = False
    notes: list[str] = []
    if not check.ok:
        notes.append("signal rejected: " + "; ".join(check.reasons))
        log.warning(notes[-1])
    else:
        rm = risk_manager.check(s, mode=mode, risk_cfg=cfg["risk"])
        if not rm.allow:
            notes.append("blocked by risk_manager: " + "; ".join(rm.reasons))
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

    paper_broker.snapshot_equity(s, mode=mode, cash=cash, current_price=current_price, symbol=symbol)
    eq, unr = paper_broker.compute_equity(s, mode=mode, current_price=current_price, symbol=symbol, cash=cash)
    _maybe_telegram(equity_msg(mode, eq, cash, unr))
    log.info(f"cycle done · equity=${eq:,.2f} cash=${cash:,.2f} unrealized=${unr:+.2f}")

    return CycleResult(symbol=symbol, action=signal.action, validated=check.ok,
                       executed=executed, skipped_reason=None, notes=notes,
                       equity=eq, cash=cash, stop_requested=False)


def _run_live(s: Session, *, cfg: dict, symbol: str, timeframes: list[str],
              snap_dict: dict, current_price: float, mode: str,
              close_all_requested: bool) -> CycleResult:
    from ai_trader.execution import binance_broker

    ex = binance_broker.make_authenticated_exchange(mode=mode)
    state = binance_broker.sync_state(s, ex, symbol=symbol, mode=mode)

    if close_all_requested:
        log.warning("close_all requested — cancelling orders and flattening")
        result = binance_broker.cancel_all_and_flatten(s, symbol=symbol, ex=ex, mode=mode)
        _maybe_telegram(f"🛑 close_all ejecutado: {result}")
        return CycleResult(symbol=symbol, action="close_all", validated=False, executed=False,
                           skipped_reason="close_all", notes=[], equity=state.cash_usdt,
                           cash=state.cash_usdt, stop_requested=True)

    exits = binance_broker.check_exits(s, symbol=symbol, exec_cfg=cfg["execution"], mode=mode, ex=ex)
    for f in exits:
        kind = "exit_tp" if f.order_price >= f.position.take_profit * 0.999 else "exit_sl"
        log.info(f"exit · {kind} · pnl=${f.position.realized_pnl:+.2f}")
        _maybe_telegram(fill_msg(symbol, f, kind))

    signal, meta = decide(
        snap_dict, model=cfg["llm"]["model"],
        max_tokens=cfg["llm"]["max_tokens"], temperature=cfg["llm"]["temperature"],
    )
    log.info(f"signal · {signal.action} entry=${signal.entry:,.2f} "
             f"sl=${signal.stop_loss:,.2f} tp=${signal.take_profit:,.2f} "
             f"rr={signal.risk_reward:.2f} conf={signal.confidence:.2f}")

    check = validate(signal, cfg["risk"])
    row = save_signal(
        s, symbol=symbol, timeframe=timeframes[0], signal=signal, check=check,
        model=meta.get("model"), usage=meta.get("usage"), snapshot=snap_dict,
    )
    _maybe_telegram(signal_msg(symbol, signal, check))

    executed = False
    notes: list[str] = []
    if not check.ok:
        notes.append("signal rejected: " + "; ".join(check.reasons))
        log.warning(notes[-1])
    else:
        rm = risk_manager.check(s, mode=mode, risk_cfg=cfg["risk"])
        if not rm.allow:
            notes.append("blocked by risk_manager: " + "; ".join(rm.reasons))
            log.warning(notes[-1])
            _maybe_telegram(risk_block_msg(rm.reasons))
        else:
            fill = binance_broker.execute_entry(
                s, signal=signal, signal_id=row.id, symbol=symbol,
                cash_available=state.cash_usdt, exec_cfg=cfg["execution"], mode=mode, ex=ex,
            )
            if fill:
                executed = True
                log.info(f"entry · qty={fill.qty:.6f} price=${fill.order_price:,.2f}")
                _maybe_telegram(fill_msg(symbol, fill, "entry"))

    eq, cash, unr = binance_broker.snapshot_equity(
        s, mode=mode, ex=ex, symbol=symbol, current_price=current_price,
    )
    _maybe_telegram(equity_msg(mode, eq, cash, unr))
    log.info(f"cycle done · equity=${eq:,.2f} cash=${cash:,.2f} unrealized=${unr:+.2f}")

    return CycleResult(symbol=symbol, action=signal.action, validated=check.ok,
                       executed=executed, skipped_reason=None, notes=notes,
                       equity=eq, cash=cash, stop_requested=False)


def run_cycle(cfg: dict, *, symbol: str | None = None, mode: str | None = None) -> CycleResult:
    init_db()
    mode = mode or MODE
    watch = cfg["watchlist"][0]
    symbol = symbol or watch["symbol"]
    timeframes = watch["timeframes"]
    lookback = watch.get("candles_lookback", 200)

    # 1) Control remoto via Telegram (idempotente si no hay credenciales).
    ctrl = telegram_control.process_pending_commands(status_snapshot=None)
    if ctrl.paused and not ctrl.stop_requested:
        log.warning("ciclo skipeado: paused via Telegram")
        return CycleResult(symbol=symbol, action="paused", validated=False, executed=False,
                           skipped_reason="paused", notes=[], equity=0.0, cash=0.0,
                           stop_requested=False)

    log.info(f"cycle start · symbol={symbol} · timeframes={timeframes} · mode={mode}")
    snap = build_market_snapshot(symbol, timeframes, lookback=lookback)
    current_price = snap.price
    log.info(f"snapshot · price=${current_price:,.2f}")

    with get_session() as s:
        if mode == "paper":
            return _run_paper(s, cfg=cfg, symbol=symbol, timeframes=timeframes,
                              snap_dict=snap.to_dict(), current_price=current_price, mode=mode)
        elif mode in ("live", "testnet"):
            return _run_live(s, cfg=cfg, symbol=symbol, timeframes=timeframes,
                             snap_dict=snap.to_dict(), current_price=current_price, mode=mode,
                             close_all_requested=ctrl.stop_requested and ctrl.paused)
        else:
            raise ValueError(f"mode desconocido: {mode}")
