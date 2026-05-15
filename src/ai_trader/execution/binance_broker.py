"""Broker real contra Binance spot vía ccxt.

Diseño:
- Entry: orden MARKET (fill garantizado, pagamos taker fee y spread).
- SL/TP: orden OCO inmediatamente después del entry (SL=stop-limit, TP=limit).
  El OCO vive en Binance: si los toca, se ejecutan aunque el bot esté caído.
- Sync de estado al arrancar: lee balance y órdenes abiertas; reconcilia con DB.

REQUISITOS PARA OPERAR EN LIVE:
1. AI_TRADER_ALLOW_LIVE=yes en .env  (doble lock).
2. --mode live al lanzar el CLI.
3. BINANCE_API_KEY/SECRET con permisos SOLO de Spot Trading (sin withdrawals).
4. Recomendado: IP whitelist en Binance restringida a la IP del bot.

Esta capa está pensada para ser probada primero en Binance Testnet
(testnet.binance.vision) usando AI_TRADER_MODE=testnet y endpoints distintos.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import ccxt
from sqlalchemy.orm import Session

from ai_trader.brain.signal import TradingSignal
from ai_trader.config import env
from ai_trader.execution.paper_broker import FillResult
from ai_trader.storage.models import Position
from ai_trader.storage.repository import (
    close_position, open_position, open_positions, record_equity, record_order,
)

log = logging.getLogger("ai_trader.binance_broker")


# ── Construcción del cliente autenticado ──────────────────────────────────────

def _testnet_urls() -> dict:
    return {
        "api": {
            "public": "https://testnet.binance.vision/api/v3",
            "private": "https://testnet.binance.vision/api/v3",
            "v3": "https://testnet.binance.vision/api/v3",
        }
    }


def _keys_for(mode: str) -> tuple[str, str]:
    """Devuelve (api_key, secret) para el modo. Usa variables específicas por
    entorno si están disponibles; cae a las genéricas para retrocompatibilidad.

    Prefijos por modo:
      testnet → BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET
      live    → BINANCE_LIVE_API_KEY    / BINANCE_LIVE_API_SECRET
      fallback genérico (cualquier modo): BINANCE_API_KEY / BINANCE_API_SECRET
    """
    prefix = "BINANCE_TESTNET" if mode == "testnet" else "BINANCE_LIVE"
    api_key = env(f"{prefix}_API_KEY") or env("BINANCE_API_KEY")
    secret = env(f"{prefix}_API_SECRET") or env("BINANCE_API_SECRET")
    if not api_key or not secret:
        raise RuntimeError(
            f"Faltan credenciales para mode={mode}. Define "
            f"{prefix}_API_KEY y {prefix}_API_SECRET en .env "
            f"(o, como fallback, BINANCE_API_KEY/BINANCE_API_SECRET)."
        )
    return api_key, secret


def make_authenticated_exchange(*, mode: str) -> ccxt.binance:
    """Crea un ccxt.binance autenticado. Verifica salvaguardas para live."""
    if mode == "live":
        if env("AI_TRADER_ALLOW_LIVE") != "yes":
            raise RuntimeError(
                "LIVE bloqueado: define AI_TRADER_ALLOW_LIVE=yes en .env "
                "para autorizar operativa con dinero real."
            )

    api_key, secret = _keys_for(mode)
    params = {
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot", "recvWindow": 10_000},
    }
    ex = ccxt.binance(params)
    if mode == "testnet":
        ex.set_sandbox_mode(True)  # ccxt swaps URLs internally
    ex.load_markets()
    return ex


# ── Helpers de precisión ──────────────────────────────────────────────────────

def _amount_to_precision(ex: ccxt.binance, symbol: str, qty: float) -> float:
    return float(ex.amount_to_precision(symbol, qty))


def _price_to_precision(ex: ccxt.binance, symbol: str, price: float) -> float:
    return float(ex.price_to_precision(symbol, price))


# ── Sync inicial: reconciliar DB con exchange ─────────────────────────────────

@dataclass
class StateSync:
    cash_usdt: float
    base_qty: float
    open_oco_orders: list[dict]
    db_open_positions: int


def sync_state(session: Session, ex: ccxt.binance, *, symbol: str, mode: str) -> StateSync:
    """Lee balance y órdenes abiertas; loguea inconsistencias con la DB."""
    base, _, quote = symbol.replace(":", "/").partition("/")
    bal = ex.fetch_balance()
    cash = float(bal.get(quote, {}).get("free", 0.0))
    base_qty = float(bal.get(base, {}).get("free", 0.0))

    open_orders = ex.fetch_open_orders(symbol)
    oco = [o for o in open_orders if (o.get("info") or {}).get("orderListId") not in (None, -1)]

    db_open = open_positions(session, symbol)
    if len(db_open) != len([o for o in oco if o.get("side") == "sell"]) // 2 and len(db_open) > 0:
        log.warning(
            f"STATE MISMATCH · DB tiene {len(db_open)} pos abiertas · "
            f"exchange tiene {len(oco)} órdenes OCO. Investigar antes de operar."
        )

    log.info(
        f"sync · {quote}={cash:.2f} · {base}={base_qty:.6f} · "
        f"open_oco={len(oco)} · db_open={len(db_open)}"
    )
    return StateSync(cash_usdt=cash, base_qty=base_qty, open_oco_orders=oco, db_open_positions=len(db_open))


# ── Entry MARKET + OCO sell ───────────────────────────────────────────────────

def execute_entry(
    session: Session,
    *,
    signal: TradingSignal,
    signal_id: int,
    symbol: str,
    cash_available: float,
    exec_cfg: dict,
    mode: str,
    ex: ccxt.binance,
) -> FillResult | None:
    """Compra MARKET y coloca OCO sell (SL stop-limit + TP limit) sobre la cantidad fillada."""
    if signal.action != "long" or signal.size_pct <= 0:
        return None

    notional = cash_available * signal.size_pct
    if notional < 10:  # Binance min ~10 USDT
        log.warning(f"notional {notional:.2f} bajo el mínimo de Binance, abortado")
        return None

    # 1) Market buy con quoteOrderQty (gasta exactamente `notional` USDT).
    order = ex.create_order(
        symbol, "market", "buy", None, None,
        params={"quoteOrderQty": _price_to_precision(ex, symbol, notional)},
    )

    filled_qty = float(order.get("filled") or order.get("amount") or 0.0)
    avg_price = float(order.get("average") or order.get("price") or 0.0)
    if filled_qty <= 0 or avg_price <= 0:
        log.error(f"market buy sin fill claro: {order}")
        return None

    fee_paid = sum(float(f.get("cost", 0)) for f in (order.get("fees") or [])) or notional * float(exec_cfg["fee_pct"])

    # 2) OCO sell — protege la posición con SL y TP simultáneos.
    qty_for_oco = _amount_to_precision(ex, symbol, filled_qty)
    tp = _price_to_precision(ex, symbol, signal.take_profit)
    sl_trigger = _price_to_precision(ex, symbol, signal.stop_loss)
    sl_limit = _price_to_precision(ex, symbol, signal.stop_loss * 0.998)  # margen para garantizar fill

    oco = ex.private_post_order_oco({
        "symbol": symbol.replace("/", ""),
        "side": "SELL",
        "quantity": qty_for_oco,
        "price": tp,
        "stopPrice": sl_trigger,
        "stopLimitPrice": sl_limit,
        "stopLimitTimeInForce": "GTC",
    })
    log.info(f"OCO colocada · orderListId={oco.get('orderListId')} · TP={tp} SL={sl_trigger}")

    # 3) Persistencia.
    pos = open_position(
        session, symbol=symbol, direction="long", qty=filled_qty,
        entry_price=avg_price, stop_loss=signal.stop_loss,
        take_profit=signal.take_profit, mode=mode, signal_id=signal_id,
    )
    record_order(
        session, symbol=symbol, side="buy", kind="entry", mode=mode,
        qty=filled_qty, price=avg_price, fee=fee_paid, signal_id=signal_id,
        external_id=str(order.get("id") or ""),
    )
    return FillResult(position=pos, order_price=avg_price, fee_paid=fee_paid, qty=filled_qty)


# ── Reconciliar salidas: si Binance ejecutó el OCO, cerrar en DB ──────────────

def check_exits(
    session: Session, *, symbol: str, exec_cfg: dict, mode: str, ex: ccxt.binance,
) -> list[FillResult]:
    """Para cada posición abierta en DB, verifica si su OCO se ejecutó en Binance."""
    fills: list[FillResult] = []
    open_pos = open_positions(session, symbol)
    if not open_pos:
        return fills

    # Estrategia simple: si NO hay órdenes OCO abiertas para este símbolo y SÍ hay
    # posiciones abiertas en DB, asumimos que se ejecutaron y leemos los últimos
    # trades del exchange para extraer precio efectivo y fees.
    open_orders = ex.fetch_open_orders(symbol)
    oco_open = any((o.get("info") or {}).get("orderListId") not in (None, -1) for o in open_orders)

    if oco_open:
        return fills  # nada que reconciliar

    # Pull recent trades to find the exit fills.
    trades = ex.fetch_my_trades(symbol, limit=20)
    sells = [t for t in trades if t["side"] == "sell"]
    if not sells:
        log.warning("no hay OCO abierto pero tampoco trades de venta recientes; investigar")
        return fills

    last_sell = sells[-1]
    fill_price = float(last_sell["price"])
    fee = float((last_sell.get("fee") or {}).get("cost", 0.0))

    for pos in open_pos:
        kind = "exit_tp" if fill_price >= pos.take_profit * 0.999 else "exit_sl"
        gross = pos.qty * (fill_price - pos.entry_price)
        entry_fee = pos.qty * pos.entry_price * float(exec_cfg["fee_pct"])
        pnl = gross - fee - entry_fee
        close_position(session, position_id=pos.id, exit_price=fill_price, realized_pnl=pnl)
        record_order(
            session, symbol=symbol, side="sell", kind=kind, mode=mode,
            qty=pos.qty, price=fill_price, fee=fee, pnl=pnl, signal_id=pos.signal_id,
            external_id=str(last_sell.get("order") or ""),
        )
        fills.append(FillResult(position=pos, order_price=fill_price, fee_paid=fee, qty=pos.qty))
    return fills


# ── Equity desde balance real ─────────────────────────────────────────────────

def snapshot_equity(session: Session, *, mode: str, ex: ccxt.binance, symbol: str,
                    current_price: float) -> tuple[float, float, float]:
    bal = ex.fetch_balance()
    base, _, quote = symbol.replace(":", "/").partition("/")
    cash = float(bal.get(quote, {}).get("total", 0.0))
    base_qty = float(bal.get(base, {}).get("total", 0.0))
    position_value = base_qty * current_price
    equity = cash + position_value

    unrealized = 0.0
    for pos in open_positions(session, symbol):
        unrealized += pos.qty * (current_price - pos.entry_price)

    record_equity(session, mode=mode, equity=equity, cash=cash, unrealized_pnl=unrealized)
    return equity, cash, unrealized


# ── Cancelación de emergencia (kill-switch) ───────────────────────────────────

def cancel_all_and_flatten(session: Session, *, symbol: str, ex: ccxt.binance, mode: str) -> dict:
    """Cancela todas las órdenes abiertas y vende todo el base a market. USAR CON CABEZA."""
    cancelled = 0
    for o in ex.fetch_open_orders(symbol):
        try:
            ex.cancel_order(o["id"], symbol)
            cancelled += 1
        except Exception as e:
            log.warning(f"cancel falló: {e}")
        time.sleep(0.2)

    bal = ex.fetch_balance()
    base, _, _ = symbol.replace(":", "/").partition("/")
    qty = float(bal.get(base, {}).get("free", 0.0))
    sell_order = None
    if qty > 0:
        sell_order = ex.create_order(symbol, "market", "sell", _amount_to_precision(ex, symbol, qty))

    for pos in open_positions(session, symbol):
        close_position(
            session, position_id=pos.id,
            exit_price=float((sell_order or {}).get("average") or 0.0),
            realized_pnl=0.0,  # se recalcula vía trades posteriores si se quiere
        )
    return {"cancelled": cancelled, "sold_qty": qty, "order": sell_order}
