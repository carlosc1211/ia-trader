import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.data.binance_client import fetch_ticker, fetch_ohlcv

t = fetch_ticker("BTC/USDT")
print(f"Ticker BTC/USDT: ${t['last']:,.2f} | 24h H ${t['high']:,.2f} L ${t['low']:,.2f} ({t['percentage']:+.2f}%)")

df = fetch_ohlcv("BTC/USDT", "4h", 5)
print(f"\nÚltimas 5 velas 4h:")
print(df.to_string())
