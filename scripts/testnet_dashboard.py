"""Dashboard de testnet por terminal: balance, órdenes abiertas y últimos trades.

Uso:
    python scripts/testnet_dashboard.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_trader.config import load_yaml
from ai_trader.execution.binance_broker import make_authenticated_exchange


def main() -> None:
    cfg = load_yaml("config.yaml")
    symbol = cfg["watchlist"][0]["symbol"]
    ex = make_authenticated_exchange(mode="testnet")

    print(f"\n========  TESTNET DASHBOARD · {symbol}  ========\n")

    # Balance USDT + BTC + valor mark-to-market.
    bal = ex.fetch_balance()
    base, _, quote = symbol.replace(":", "/").partition("/")
    usdt = float(bal.get(quote, {}).get("free", 0.0))
    btc_free = float(bal.get(base, {}).get("free", 0.0))
    btc_locked = float(bal.get(base, {}).get("used", 0.0))
    ticker = ex.fetch_ticker(symbol)
    price = float(ticker["last"])

    equity = usdt + (btc_free + btc_locked) * price
    print(f"Precio actual:  ${price:,.2f}")
    print(f"USDT libre:     ${usdt:,.2f}")
    print(f"BTC libre:      {btc_free:.8f}")
    print(f"BTC en OCO:     {btc_locked:.8f}")
    print(f"Equity (mark):  ${equity:,.2f}\n")

    # Órdenes abiertas.
    opens = ex.fetch_open_orders(symbol)
    print(f"── Órdenes abiertas ({len(opens)}) ──")
    if not opens:
        print("  (ninguna)\n")
    for o in opens:
        info = o.get("info") or {}
        list_id = info.get("orderListId")
        stop = info.get("stopPrice") or "-"
        ttype = o.get("type")
        side = o.get("side")
        qty = float(o.get("amount") or 0)
        prc = o.get("price")
        flag = f"OCO#{list_id}" if list_id not in (None, -1, "-1") else "single"
        print(f"  [{flag}] {ttype:>14} {side:>4} qty={qty:.8f} price={prc} stop={stop}  status={o.get('status')}")
    print()

    # Últimos trades.
    trades = ex.fetch_my_trades(symbol, limit=5)
    print(f"── Últimos {len(trades)} trades ──")
    if not trades:
        print("  (ninguno)\n")
    for t in trades:
        side = t.get("side")
        prc = float(t.get("price") or 0)
        qty = float(t.get("amount") or 0)
        fee = (t.get("fee") or {}).get("cost") or 0
        ts = t.get("datetime")
        print(f"  {ts}  {side:>4}  qty={qty:.8f} price=${prc:,.2f} fee={fee}")
    print()


if __name__ == "__main__":
    main()
