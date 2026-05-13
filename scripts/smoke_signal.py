"""Smoke test fase 2: snapshot → Claude → señal validada.

Uso:
    python scripts/smoke_signal.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.brain.claude_engine import decide
from ai_trader.brain.risk_validator import validate
from ai_trader.config import load_yaml
from ai_trader.data.market_snapshot import build_market_snapshot


def main() -> None:
    cfg = load_yaml("config.yaml")
    symbol = cfg["watchlist"][0]["symbol"]
    timeframes = cfg["watchlist"][0]["timeframes"]
    lookback = cfg["watchlist"][0].get("candles_lookback", 200)

    print(f"→ Snapshot {symbol} {timeframes}...")
    snap = build_market_snapshot(symbol, timeframes, lookback=lookback)
    print(f"  precio actual: ${snap.price:,.2f}")

    print(f"→ Pidiendo decisión a Claude ({cfg['llm']['model']})...")
    signal, meta = decide(
        snap.to_dict(),
        model=cfg["llm"]["model"],
        max_tokens=cfg["llm"]["max_tokens"],
        temperature=cfg["llm"]["temperature"],
    )

    print("\n── Señal ──")
    print(json.dumps(signal.model_dump(), indent=2, ensure_ascii=False))
    print(f"\nRR: {signal.risk_reward:.2f}")

    check = validate(signal, cfg["risk"])
    print(f"\n── Validación de riesgo: {'OK' if check.ok else 'RECHAZADA'} ──")
    for r in check.reasons:
        print(f"  - {r}")

    print(f"\n── Meta ──")
    print(json.dumps(meta, indent=2, default=str))


if __name__ == "__main__":
    main()
