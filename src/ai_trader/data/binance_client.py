from __future__ import annotations
import ccxt
import pandas as pd
from ai_trader.config import env


def make_exchange(authenticated: bool = False) -> ccxt.binance:
    params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
    if authenticated:
        params["apiKey"] = env("BINANCE_API_KEY", required=True)
        params["secret"] = env("BINANCE_API_SECRET", required=True)
    return ccxt.binance(params)


def fetch_ticker(symbol: str = "BTC/USDT") -> dict:
    ex = make_exchange()
    return ex.fetch_ticker(symbol)


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    ex = make_exchange()
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    return df
