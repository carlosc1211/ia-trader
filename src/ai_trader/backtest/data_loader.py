"""Descarga paginada de OHLCV histórico desde Binance.

ccxt limita a ~1000 velas por request. Esta capa hace múltiples llamadas
respetando rate-limit y devuelve un único DataFrame ordenado por tiempo.
También cachea a parquet/csv en data/cache/ para no descargar dos veces.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ai_trader.config import ROOT
from ai_trader.data.binance_client import make_exchange


_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440,
}


def _cache_path(symbol: str, timeframe: str, start: datetime, end: datetime) -> Path:
    cache_dir = ROOT / "data" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("/", "")
    return cache_dir / f"{safe}_{timeframe}_{start:%Y%m%d}_{end:%Y%m%d}.csv"


def load_history(symbol: str, timeframe: str, start: datetime, end: datetime,
                 use_cache: bool = True) -> pd.DataFrame:
    """Descarga OHLCV entre [start, end] inclusive. Tiempos en UTC."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    cache = _cache_path(symbol, timeframe, start, end)
    if use_cache and cache.exists():
        return pd.read_csv(cache, index_col=0, parse_dates=[0])

    ex = make_exchange()
    tf_min = _TF_MINUTES[timeframe]
    limit = 1000

    rows: list[list] = []
    since_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    while since_ms < end_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        next_since = last_ts + tf_min * 60_000
        if next_since <= since_ms:
            break
        since_ms = next_since
        time.sleep(ex.rateLimit / 1000)

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df[(df["ts"] >= start) & (df["ts"] <= end)].set_index("ts").sort_index()

    if use_cache:
        df.to_csv(cache)
    return df
