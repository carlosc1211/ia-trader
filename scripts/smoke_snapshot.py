"""Smoke test fase 1: snapshot completo de BTC/USDT en 4h y 1d.

Uso:
    python scripts/smoke_snapshot.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.data.market_snapshot import build_market_snapshot


def main() -> None:
    snap = build_market_snapshot("BTC/USDT", ["4h", "1d"], lookback=200)
    print(json.dumps(snap.to_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
