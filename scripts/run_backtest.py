"""CLI de backtest.

Uso:
    python scripts/run_backtest.py --days 30
    python scripts/run_backtest.py --start 2026-03-01 --end 2026-04-30 --no-cache
    python scripts/run_backtest.py --days 7 --yes
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.backtest.engine import run_backtest
from ai_trader.backtest.report import compute, print_report
from ai_trader.config import load_yaml
from ai_trader.logging_setup import setup as setup_logging


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=None)
    p.add_argument("--timeframe", default=None)
    p.add_argument("--days", type=int, default=None, help="ventana desde hoy hacia atrás")
    p.add_argument("--start", help="YYYY-MM-DD")
    p.add_argument("--end", help="YYYY-MM-DD")
    p.add_argument("--no-cache", action="store_true", help="ignora caché LLM (paga todo)")
    p.add_argument("--prompt", default="v2", choices=["v1", "v2"], help="versión del system prompt")
    p.add_argument("--yes", action="store_true", help="no preguntar antes de ejecutar")
    args = p.parse_args()

    setup_logging()
    cfg = load_yaml("config.yaml")
    watch = cfg["watchlist"][0]
    symbol = args.symbol or watch["symbol"]
    timeframe = args.timeframe or watch["timeframes"][0]

    if args.start and args.end:
        start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    elif args.days:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=args.days + 35)  # +35 días warmup p/ 200 velas 4h
    else:
        print("Pasa --days N o --start/--end.")
        return 2

    bars_estimate = int((end - start).total_seconds() / 60 / 240)  # 4h
    decisions_est = max(0, bars_estimate - 200)
    cost_est = decisions_est * 0.017

    print(f"\nSymbol: {symbol} {timeframe}")
    print(f"Rango:  {start:%Y-%m-%d} → {end:%Y-%m-%d}")
    print(f"Barras estimadas: {bars_estimate} (decisiones ~{decisions_est})")
    print(f"Coste estimado SIN caché: ~${cost_est:.2f}")
    print(f"Caché LLM: {'OFF' if args.no_cache else 'ON'}")
    print(f"Prompt:    {args.prompt}")

    if not args.yes:
        resp = input("¿Continuar? [y/N]: ").strip().lower()
        if resp != "y":
            print("Cancelado.")
            return 1

    result = run_backtest(
        symbol=symbol, timeframe=timeframe, start=start, end=end,
        cfg=cfg, use_cache=not args.no_cache, prompt_version=args.prompt,
    )
    metrics = compute(result, initial_capital=float(cfg["execution"]["initial_capital_usdt"]))
    print_report(metrics, initial_capital=float(cfg["execution"]["initial_capital_usdt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
