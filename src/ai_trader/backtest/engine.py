"""Engine de backtest: replay del loop sobre histórico sin look-ahead.

Para cada vela de cierre dentro del rango, construye un snapshot que solo
usa velas anteriores o iguales a esa fecha. Reusa toda la lógica de
features, signal, risk_validator y paper_broker en memoria — pero sin DB
para no contaminar el estado real.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import pandas as pd

from ai_trader.backtest.data_loader import load_history
from ai_trader.backtest.llm_cache import get as cache_get, put as cache_put, snapshot_key
from ai_trader.brain.claude_engine import decide
from ai_trader.brain.risk_validator import validate
from ai_trader.brain.signal import TradingSignal
from ai_trader.data.features import add_indicators, support_resistance

log = logging.getLogger("ai_trader.backtest")


@dataclass
class BTTrade:
    entry_ts: datetime
    entry_price: float
    qty: float
    stop_loss: float
    take_profit: float
    exit_ts: datetime | None = None
    exit_price: float | None = None
    exit_kind: str | None = None
    pnl: float | None = None
    signal: TradingSignal | None = None


@dataclass
class BTResult:
    trades: list[BTTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    decisions: int = 0
    flat_count: int = 0
    rejected: int = 0
    cache_hits: int = 0
    llm_calls: int = 0


def _snapshot_at(df: pd.DataFrame, ts: pd.Timestamp, tf_label: str,
                 symbol: str, lookback: int = 200) -> dict:
    sub = df.loc[:ts].tail(lookback)
    feats = add_indicators(sub)
    last = feats.iloc[-1]

    def _f(x): return float(x) if pd.notna(x) else None

    indicators = {
        k: round(_f(last[k]), 4)
        for k in ["ema20","ema50","ema200","rsi14","atr14","macd","macd_signal","macd_hist",
                  "bb_lower","bb_mid","bb_upper","bb_width","vol_rel"]
        if k in feats.columns and pd.notna(last[k])
    }
    levels = support_resistance(feats, lookback=min(50, len(feats)))
    recent = [
        {"ts": idx.isoformat(), "o": round(r["open"],2), "h": round(r["high"],2),
         "l": round(r["low"],2), "c": round(r["close"],2), "v": round(r["volume"],2)}
        for idx, r in feats.tail(8).iterrows()
    ]
    return {
        "symbol": symbol,
        "price": round(float(last["close"]), 2),
        "change_24h_pct": None,
        "timeframes": [{
            "timeframe": tf_label,
            "last_close": round(float(last["close"]), 2),
            "indicators": indicators,
            "levels": levels,
            "recent_candles": recent,
        }],
    }


def _decide_cached(snap: dict, model: str, max_tokens: int, temperature: float,
                   use_cache: bool, result: BTResult, prompt_version: str,
                   ) -> tuple[TradingSignal, dict]:
    key = snapshot_key(snap, model, prompt_version=prompt_version)
    if use_cache:
        hit = cache_get(key)
        if hit:
            result.cache_hits += 1
            return hit
    signal, meta = decide(
        snap, model=model, max_tokens=max_tokens, temperature=temperature,
        prompt_version=prompt_version,
    )
    result.llm_calls += 1
    if use_cache:
        cache_put(key, signal, meta)
    return signal, meta


def _exit_check(price_high: float, price_low: float, trade: BTTrade) -> str | None:
    # SL gana en caso de ambigüedad (peor caso) — asunción conservadora.
    if price_low <= trade.stop_loss:
        return "exit_sl"
    if price_high >= trade.take_profit:
        return "exit_tp"
    return None


def run_backtest(
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    cfg: dict,
    warmup_bars: int = 200,
    use_cache: bool = True,
    prompt_version: str = "v2",
) -> BTResult:
    df = load_history(symbol, timeframe, start, end, use_cache=True)
    if len(df) < warmup_bars + 2:
        raise ValueError(f"Histórico insuficiente: {len(df)} velas, mínimo {warmup_bars + 2}")

    cash = float(cfg["execution"]["initial_capital_usdt"])
    slippage = float(cfg["execution"]["slippage_pct"])
    fee_pct = float(cfg["execution"]["fee_pct"])
    risk_cfg = cfg["risk"]
    llm_cfg = cfg["llm"]

    open_trade: BTTrade | None = None
    result = BTResult()

    bars = df.iloc[warmup_bars:]
    log.info(f"backtest · {symbol} {timeframe} · bars={len(bars)} cache={use_cache} prompt={prompt_version}")

    for ts, bar in bars.iterrows():
        # 1) Salida de la posición si SL/TP se tocan en esta vela.
        if open_trade:
            exit_kind = _exit_check(float(bar["high"]), float(bar["low"]), open_trade)
            if exit_kind:
                exit_price_raw = open_trade.stop_loss if exit_kind == "exit_sl" else open_trade.take_profit
                fill_price = exit_price_raw * (1 - slippage)
                gross = open_trade.qty * (fill_price - open_trade.entry_price)
                fees = open_trade.qty * (fill_price + open_trade.entry_price) * fee_pct
                pnl = gross - fees
                cash += open_trade.qty * fill_price - open_trade.qty * fill_price * fee_pct
                open_trade.exit_ts = ts
                open_trade.exit_price = fill_price
                open_trade.exit_kind = exit_kind
                open_trade.pnl = pnl
                result.trades.append(open_trade)
                open_trade = None

        # 2) Decisión LLM solo si no hay posición abierta (1 simultánea en BT inicial).
        if open_trade is None:
            snap = _snapshot_at(df, ts, timeframe, symbol, lookback=warmup_bars)
            signal, _ = _decide_cached(
                snap, llm_cfg["model"], llm_cfg["max_tokens"], llm_cfg["temperature"],
                use_cache, result, prompt_version,
            )
            result.decisions += 1
            if signal.action == "flat":
                result.flat_count += 1
            else:
                check = validate(signal, risk_cfg)
                if not check.ok:
                    result.rejected += 1
                elif signal.action == "long" and signal.size_pct > 0:
                    notional = cash * signal.size_pct
                    fill_price = float(bar["close"]) * (1 + slippage)
                    qty = notional / fill_price
                    entry_fee = notional * fee_pct
                    cash -= notional + entry_fee
                    open_trade = BTTrade(
                        entry_ts=ts, entry_price=fill_price, qty=qty,
                        stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                        signal=signal,
                    )

        # 3) Equity mark-to-market con el cierre de la vela.
        mv = open_trade.qty * float(bar["close"]) if open_trade else 0.0
        result.equity_curve.append((ts.to_pydatetime(), cash + mv))

    return result
