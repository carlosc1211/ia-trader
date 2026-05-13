"""Métricas y reporte resumido de un backtest."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ai_trader.backtest.engine import BTResult


@dataclass
class Metrics:
    n_trades: int
    win_rate: float
    avg_rr: float
    pnl_total: float
    pnl_pct: float
    max_drawdown_pct: float
    sharpe_proxy: float
    decisions: int
    flats: int
    rejected: int
    cache_hits: int
    llm_calls: int


def compute(result: BTResult, initial_capital: float) -> Metrics:
    wins = [t for t in result.trades if (t.pnl or 0) > 0]
    win_rate = len(wins) / len(result.trades) if result.trades else 0.0

    rrs = []
    for t in result.trades:
        if not t.signal or t.signal.action != "long":
            continue
        risk = t.entry_price - t.signal.stop_loss
        reward = (t.exit_price or t.entry_price) - t.entry_price
        if risk > 0:
            rrs.append(reward / risk)
    avg_rr = sum(rrs) / len(rrs) if rrs else 0.0

    pnl_total = sum((t.pnl or 0) for t in result.trades)
    pnl_pct = pnl_total / initial_capital * 100 if initial_capital else 0.0

    peak = initial_capital
    max_dd = 0.0
    for _, eq in result.equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    if len(result.equity_curve) > 1:
        rets = []
        prev = result.equity_curve[0][1]
        for _, eq in result.equity_curve[1:]:
            if prev > 0:
                rets.append((eq - prev) / prev)
            prev = eq
        if rets and len(rets) > 1:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            std = math.sqrt(var) if var > 0 else 0.0
            sharpe = (mean / std) * math.sqrt(365 * 6) if std > 0 else 0.0
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    return Metrics(
        n_trades=len(result.trades),
        win_rate=win_rate,
        avg_rr=avg_rr,
        pnl_total=pnl_total,
        pnl_pct=pnl_pct,
        max_drawdown_pct=max_dd * 100,
        sharpe_proxy=sharpe,
        decisions=result.decisions,
        flats=result.flat_count,
        rejected=result.rejected,
        cache_hits=result.cache_hits,
        llm_calls=result.llm_calls,
    )


def print_report(metrics: Metrics, initial_capital: float) -> None:
    print("\n========== BACKTEST REPORT ==========")
    print(f"Trades ejecutados:   {metrics.n_trades}")
    print(f"Win rate:            {metrics.win_rate*100:.1f}%")
    print(f"RR medio realizado:  {metrics.avg_rr:.2f}")
    print(f"PnL total:           ${metrics.pnl_total:+,.2f} ({metrics.pnl_pct:+.2f}%)")
    print(f"Equity final:        ${initial_capital + metrics.pnl_total:,.2f}")
    print(f"Max drawdown:        {metrics.max_drawdown_pct:.2f}%")
    print(f"Sharpe (proxy 4h):   {metrics.sharpe_proxy:.2f}")
    print("---- Decisiones LLM ----")
    print(f"Total:               {metrics.decisions}")
    print(f"  · flat:            {metrics.flats}")
    print(f"  · rejected:        {metrics.rejected}")
    print(f"  · operadas:        {metrics.n_trades}")
    print(f"Cache hits:          {metrics.cache_hits}")
    print(f"LLM calls reales:    {metrics.llm_calls}  (~${metrics.llm_calls*0.017:.2f})")
    print("=====================================\n")
