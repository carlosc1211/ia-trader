"""Cálculo de indicadores técnicos sobre un DataFrame OHLCV.

Convención: el DataFrame de entrada tiene índice temporal UTC y columnas
['open','high','low','close','volume']. Las funciones devuelven un nuevo
DataFrame con las columnas originales más los indicadores añadidos.
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["ema20"] = ta.ema(out["close"], length=20)
    out["ema50"] = ta.ema(out["close"], length=50)
    out["ema200"] = ta.ema(out["close"], length=200)

    out["rsi14"] = ta.rsi(out["close"], length=14)
    out["atr14"] = ta.atr(out["high"], out["low"], out["close"], length=14)

    macd = ta.macd(out["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        out["macd"] = macd["MACD_12_26_9"]
        out["macd_signal"] = macd["MACDs_12_26_9"]
        out["macd_hist"] = macd["MACDh_12_26_9"]

    bb = ta.bbands(out["close"], length=20, std=2)
    if bb is not None:
        lower = next(c for c in bb.columns if c.startswith("BBL_"))
        mid = next(c for c in bb.columns if c.startswith("BBM_"))
        upper = next(c for c in bb.columns if c.startswith("BBU_"))
        out["bb_lower"] = bb[lower]
        out["bb_mid"] = bb[mid]
        out["bb_upper"] = bb[upper]
        out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / out["bb_mid"]

    out["vol_sma20"] = out["volume"].rolling(20).mean()
    out["vol_rel"] = out["volume"] / out["vol_sma20"]

    return out


def support_resistance(df: pd.DataFrame, lookback: int = 50, n_levels: int = 3) -> dict:
    window = df.tail(lookback)
    highs = window["high"].nlargest(n_levels).tolist()
    lows = window["low"].nsmallest(n_levels).tolist()
    return {"resistance": sorted(set(highs), reverse=True), "support": sorted(set(lows))}
