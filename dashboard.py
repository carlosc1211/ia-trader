"""Dashboard local del bot (Streamlit).

Uso:
    .venv\Scripts\streamlit run dashboard.py

Se abre en http://localhost:8501. Lee directamente de la SQLite local y
del exchange (si hay credenciales). Auto-refresh cada 30s.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pandas as pd
import streamlit as st
from sqlalchemy import select

from ai_trader.config import load_yaml
from ai_trader.storage.db import get_session
from ai_trader.storage.models import EquitySnapshot, Order, Position, Signal


st.set_page_config(page_title="ai-trader · dashboard", page_icon="📈", layout="wide")
st.title("📈 ai-trader · dashboard")

# Auto-refresh.
with st.sidebar:
    st.header("Controles")
    mode_filter = st.selectbox("Modo", ["all", "paper", "testnet", "live"], index=0)
    auto_refresh = st.checkbox("Auto-refresh 30s", value=False)
    if st.button("Refrescar ahora"):
        st.rerun()
    st.divider()
    st.caption("DB: data/ai_trader.sqlite")

if auto_refresh:
    st.markdown("<meta http-equiv='refresh' content='30'>", unsafe_allow_html=True)


def _filter_mode(query, model, mode: str):
    return query if mode == "all" else query.where(model.mode == mode)


# ── Métricas en cabecera ──────────────────────────────────────────────────────
with get_session() as s:
    stmt = select(EquitySnapshot).order_by(EquitySnapshot.ts.desc()).limit(1)
    stmt = _filter_mode(stmt, EquitySnapshot, mode_filter)
    last_eq = s.scalars(stmt).first()

    open_pos_stmt = select(Position).where(Position.closed_at.is_(None))
    open_pos_stmt = _filter_mode(open_pos_stmt, Position, mode_filter)
    open_pos = list(s.scalars(open_pos_stmt))

    closed_pos_stmt = select(Position).where(Position.closed_at.is_not(None))
    closed_pos_stmt = _filter_mode(closed_pos_stmt, Position, mode_filter)
    closed_pos = list(s.scalars(closed_pos_stmt))

    total_pnl = sum(p.realized_pnl or 0 for p in closed_pos)
    wins = sum(1 for p in closed_pos if (p.realized_pnl or 0) > 0)
    win_rate = (wins / len(closed_pos) * 100) if closed_pos else 0.0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Equity actual", f"${last_eq.equity:,.2f}" if last_eq else "—")
c2.metric("Cash", f"${last_eq.cash:,.2f}" if last_eq else "—")
c3.metric("Posiciones abiertas", len(open_pos))
c4.metric("PnL realizado", f"${total_pnl:+,.2f}", f"WR {win_rate:.0f}%  ·  {len(closed_pos)} trades")

# ── Curva de equity ───────────────────────────────────────────────────────────
st.subheader("Curva de equity")
with get_session() as s:
    stmt = select(EquitySnapshot).order_by(EquitySnapshot.ts.asc())
    stmt = _filter_mode(stmt, EquitySnapshot, mode_filter)
    eq_rows = list(s.scalars(stmt))

if eq_rows:
    df_eq = pd.DataFrame([{"ts": r.ts, "equity": r.equity, "cash": r.cash, "mode": r.mode} for r in eq_rows])
    st.line_chart(df_eq.set_index("ts")[["equity", "cash"]])
else:
    st.info("Sin datos de equity todavía. Lanza un ciclo del bot.")

# ── Posiciones abiertas ───────────────────────────────────────────────────────
st.subheader(f"Posiciones abiertas ({len(open_pos)})")
if open_pos:
    df_op = pd.DataFrame([{
        "id": p.id, "symbol": p.symbol, "dir": p.direction, "qty": p.qty,
        "entry": p.entry_price, "SL": p.stop_loss, "TP": p.take_profit,
        "mode": p.mode, "opened": p.opened_at,
    } for p in open_pos])
    st.dataframe(df_op, hide_index=True, use_container_width=True)
else:
    st.caption("Sin posiciones abiertas.")

# ── Posiciones cerradas ───────────────────────────────────────────────────────
st.subheader(f"Histórico cerradas ({len(closed_pos)})")
if closed_pos:
    rows = []
    for p in closed_pos:
        notional = p.qty * p.entry_price
        pnl = p.realized_pnl or 0.0
        pnl_pct = (pnl / notional * 100) if notional > 0 else 0.0
        dur = (p.closed_at - p.opened_at) if p.closed_at else None
        rows.append({
            "id": p.id, "symbol": p.symbol, "dir": p.direction,
            "notional": notional,
            "entry": p.entry_price, "exit": p.exit_price,
            "PnL $": pnl, "PnL %": pnl_pct,
            "duración": str(dur).split(".")[0] if dur else "",
            "mode": p.mode,
            "opened": p.opened_at, "closed": p.closed_at,
        })
    df_cl = pd.DataFrame(rows).sort_values("closed", ascending=False)

    st.dataframe(
        df_cl, hide_index=True, use_container_width=True,
        column_config={
            "notional": st.column_config.NumberColumn("Notional", format="$%.2f"),
            "entry":    st.column_config.NumberColumn("Entry", format="$%.2f"),
            "exit":     st.column_config.NumberColumn("Exit", format="$%.2f"),
            "PnL $":    st.column_config.NumberColumn("PnL $", format="$%+.2f"),
            "PnL %":    st.column_config.NumberColumn("PnL %", format="%+.2f%%"),
        },
    )

    # Resumen agregado debajo del histórico.
    total_pnl_d = df_cl["PnL $"].sum()
    total_notional = df_cl["notional"].sum()
    avg_pct = df_cl["PnL %"].mean()
    best = df_cl["PnL $"].max()
    worst = df_cl["PnL $"].min()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("PnL acumulado", f"${total_pnl_d:+,.2f}")
    c2.metric("PnL medio %", f"{avg_pct:+.2f}%")
    c3.metric("Mejor trade", f"${best:+,.2f}")
    c4.metric("Peor trade", f"${worst:+,.2f}")

# ── Últimas señales ───────────────────────────────────────────────────────────
st.subheader("Últimas señales de Claude")
with get_session() as s:
    stmt = select(Signal).order_by(Signal.created_at.desc()).limit(50)
    sigs = list(s.scalars(stmt))

if sigs:
    df_sig = pd.DataFrame([{
        "ts": x.created_at, "symbol": x.symbol, "tf": x.timeframe,
        "action": x.action, "conf": x.confidence, "RR": round(x.risk_reward, 2),
        "size%": round(x.size_pct * 100, 2), "entry": x.entry,
        "SL": x.stop_loss, "TP": x.take_profit, "valid": x.validated,
        "rationale": (x.rationale or "")[:200],
    } for x in sigs])
    st.dataframe(df_sig, hide_index=True, use_container_width=True, height=400)
else:
    st.caption("Sin señales aún.")

# ── Últimos fills ─────────────────────────────────────────────────────────────
st.subheader("Últimos fills")
with get_session() as s:
    stmt = select(Order).order_by(Order.created_at.desc()).limit(30)
    orders = list(s.scalars(stmt))

if orders:
    df_o = pd.DataFrame([{
        "ts": o.created_at, "symbol": o.symbol, "side": o.side, "kind": o.kind,
        "qty": o.qty, "price": o.price, "fee": o.fee, "PnL": o.pnl, "mode": o.mode,
    } for o in orders])
    st.dataframe(df_o, hide_index=True, use_container_width=True)
