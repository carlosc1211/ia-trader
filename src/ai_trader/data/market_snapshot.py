"""Snapshot compacto de mercado listo para inyectar al prompt de Claude.

Cada snapshot resume un símbolo en uno o varios timeframes: precio actual,
indicadores en la última vela, niveles S/R y un mini-resumen de las últimas
velas. La salida está pensada para ser serializable a JSON/texto y caber
holgadamente en el contexto del LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

import pandas as pd

from ai_trader.data.binance_client import fetch_ohlcv, fetch_ticker
from ai_trader.data.features import add_indicators, support_resistance


@dataclass
class TimeframeSnapshot:
    timeframe: str
    last_close: float
    indicators: dict
    levels: dict
    recent_candles: list[dict]


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    change_24h_pct: float | None
    timeframes: list[TimeframeSnapshot]

    def to_dict(self) -> dict:
        return asdict(self)


def _round_dict(d: dict, ndigits: int = 4) -> dict:
    return {k: (round(v, ndigits) if isinstance(v, (int, float)) and v == v else v) for k, v in d.items()}


def _last_indicators(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    keys = [
        "ema20", "ema50", "ema200",
        "rsi14", "atr14",
        "macd", "macd_signal", "macd_hist",
        "bb_lower", "bb_mid", "bb_upper", "bb_width",
        "vol_rel",
    ]
    return _round_dict({k: float(last[k]) for k in keys if k in df.columns and pd.notna(last[k])})


def _recent_candles(df: pd.DataFrame, n: int = 8) -> list[dict]:
    tail = df.tail(n)
    return [
        {
            "ts": ts.isoformat(),
            "o": round(float(r["open"]), 2),
            "h": round(float(r["high"]), 2),
            "l": round(float(r["low"]), 2),
            "c": round(float(r["close"]), 2),
            "v": round(float(r["volume"]), 2),
        }
        for ts, r in tail.iterrows()
    ]


def build_timeframe_snapshot(symbol: str, timeframe: str, lookback: int = 200) -> TimeframeSnapshot:
    raw = fetch_ohlcv(symbol, timeframe, limit=lookback)
    df = add_indicators(raw)
    return TimeframeSnapshot(
        timeframe=timeframe,
        last_close=round(float(df["close"].iloc[-1]), 2),
        indicators=_last_indicators(df),
        levels=support_resistance(df, lookback=min(50, len(df))),
        recent_candles=_recent_candles(df, n=8),
    )


def build_market_snapshot(symbol: str, timeframes: Iterable[str], lookback: int = 200) -> MarketSnapshot:
    ticker = fetch_ticker(symbol)
    tfs = [build_timeframe_snapshot(symbol, tf, lookback=lookback) for tf in timeframes]
    return MarketSnapshot(
        symbol=symbol,
        price=round(float(ticker["last"]), 2),
        change_24h_pct=round(float(ticker["percentage"]), 2) if ticker.get("percentage") is not None else None,
        timeframes=tfs,
    )
