"""CLI: python -m ai_trader [run|once] [--mode paper|testnet|live]

run  → bucle continuo, espera config.decision_loop.interval_minutes entre ciclos.
once → ejecuta un único ciclo y sale (útil con cron / Task Scheduler).
"""
from __future__ import annotations

import argparse
import sys
import time

from ai_trader.config import load_yaml
from ai_trader.logging_setup import setup as setup_logging
from ai_trader.scheduler import run_cycle


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai_trader")
    sub = p.add_subparsers(dest="cmd", required=True)

    common_args = lambda sp: (
        sp.add_argument("--mode", default=None, choices=["paper", "testnet", "live"]),
        sp.add_argument("--symbol", default=None, help="override watchlist[0].symbol"),
        sp.add_argument("--config", default="config.yaml"),
    )

    p_once = sub.add_parser("once", help="ejecuta un solo ciclo y sale")
    common_args(p_once)

    p_run = sub.add_parser("run", help="bucle continuo")
    common_args(p_run)

    return p


def main(argv: list[str] | None = None) -> int:
    log = setup_logging()
    args = _build_parser().parse_args(argv)
    cfg = load_yaml(args.config)

    if args.cmd == "once":
        result = run_cycle(cfg, symbol=args.symbol, mode=args.mode)
        log.info(f"result: action={result.action} executed={result.executed} equity=${result.equity:,.2f}")
        return 0

    interval = int(cfg["decision_loop"]["interval_minutes"]) * 60
    log.info(f"entering loop · interval={interval}s")
    while True:
        try:
            run_cycle(cfg, symbol=args.symbol, mode=args.mode)
        except Exception:
            log.exception("cycle failed")
        log.info(f"sleeping {interval}s")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
