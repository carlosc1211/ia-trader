"""Formato de mensajes Telegram (HTML).

Mensajes cortos, legibles en móvil. El rationale del LLM puede ser largo,
así que lo truncamos a un límite razonable.
"""
from __future__ import annotations

from ai_trader.brain.risk_validator import RiskCheck
from ai_trader.brain.signal import TradingSignal
from ai_trader.execution.paper_broker import FillResult


def _trim(text: str, n: int = 400) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def signal_msg(symbol: str, signal: TradingSignal, check: RiskCheck) -> str:
    icon = {"long": "🟢", "short": "🔴", "flat": "⚪"}[signal.action]
    valid_icon = "✅" if check.ok else "🚫"
    lines = [
        f"<b>{icon} {symbol} — {signal.action.upper()}</b>",
        f"Entry: <b>${signal.entry:,.2f}</b>  |  SL: ${signal.stop_loss:,.2f}  |  TP: ${signal.take_profit:,.2f}",
        f"Size: {signal.size_pct*100:.1f}%  ·  RR: {signal.risk_reward:.2f}  ·  Conf: {signal.confidence*100:.0f}%",
        f"Validación: {valid_icon}",
    ]
    if not check.ok:
        for r in check.reasons:
            lines.append(f"  · {r}")
    lines.append(f"\n<i>{_trim(signal.rationale)}</i>")
    return "\n".join(lines)


def fill_msg(symbol: str, fill: FillResult, kind: str) -> str:
    icon = {"entry": "🟢", "exit_sl": "🔻", "exit_tp": "🎯", "exit_manual": "⏹️"}.get(kind, "▫️")
    pnl_line = ""
    if fill.position.realized_pnl is not None:
        pnl_line = f"\nPnL realizado: <b>${fill.position.realized_pnl:+.2f}</b>"
    return (
        f"<b>{icon} {symbol} — {kind.upper()}</b>\n"
        f"Qty: {fill.qty:.6f}  ·  Precio: ${fill.order_price:,.2f}\n"
        f"Fee: ${fill.fee_paid:.4f}"
        f"{pnl_line}"
    )


def equity_msg(mode: str, equity: float, cash: float, unrealized_pnl: float) -> str:
    return (
        f"<b>💼 Equity ({mode})</b>\n"
        f"Total: <b>${equity:,.2f}</b>  ·  Cash: ${cash:,.2f}\n"
        f"Unrealized: ${unrealized_pnl:+.2f}"
    )


def risk_block_msg(reasons: list[str]) -> str:
    body = "\n".join(f"  · {r}" for r in reasons)
    return f"<b>🚧 Ejecución bloqueada por risk manager</b>\n{body}"
