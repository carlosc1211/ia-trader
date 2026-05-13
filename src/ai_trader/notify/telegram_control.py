"""Kill-switch y control remoto del bot por Telegram (polling).

No usamos webhooks (requeriría servidor público). En su lugar, al inicio de
cada ciclo el scheduler llama a `process_pending_commands()` que consume
`getUpdates` y aplica el comando.

Estado persistido en data/control.json:
  paused: bool         — si True, el scheduler skipea el ciclo (no decide)
  stop_requested: bool — si True, el loop sale tras el ciclo actual
  last_update_id: int  — para no procesar el mismo comando dos veces

Comandos soportados:
  /status    → estado actual + última equity
  /pause     → marca paused=True
  /resume    → marca paused=False
  /stop      → marca stop_requested=True (sale del loop)
  /close_all → cancela órdenes + vende todo (SOLO live/testnet)
  /help      → lista comandos
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from ai_trader.config import ROOT, env

log = logging.getLogger("ai_trader.telegram_control")


@dataclass
class Control:
    paused: bool = False
    stop_requested: bool = False
    last_update_id: int = 0


def _path() -> Path:
    p = ROOT / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p / "control.json"


def load() -> Control:
    f = _path()
    if not f.exists():
        return Control()
    data = json.loads(f.read_text(encoding="utf-8"))
    return Control(**data)


def save(state: Control) -> None:
    _path().write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def _send(text: str) -> None:
    token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"telegram send failed: {e}")


def _get_updates(offset: int) -> list[dict]:
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        return []
    try:
        r = httpx.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": 0, "allowed_updates": ["message"]},
            timeout=10,
        )
        return (r.json().get("result") or []) if r.is_success else []
    except Exception as e:
        log.warning(f"telegram getUpdates failed: {e}")
        return []


HELP = (
    "<b>Comandos disponibles</b>\n"
    "/status — estado actual\n"
    "/pause — pausar decisiones\n"
    "/resume — reanudar\n"
    "/stop — salir del loop tras el ciclo actual\n"
    "/close_all — cancela OCO y vende todo (live/testnet)\n"
    "/help — esta ayuda"
)


def process_pending_commands(*, status_snapshot: str | None = None) -> Control:
    """Lee updates pendientes y aplica comandos. Devuelve el estado tras procesarlos."""
    state = load()
    chat_id_str = env("TELEGRAM_CHAT_ID")
    if not chat_id_str:
        return state

    expected_chat_id = int(chat_id_str)
    updates = _get_updates(state.last_update_id + 1)
    if not updates:
        return state

    for upd in updates:
        state.last_update_id = max(state.last_update_id, int(upd["update_id"]))
        msg = upd.get("message") or {}
        if (msg.get("chat") or {}).get("id") != expected_chat_id:
            continue  # ignorar comandos de otros chats
        text = (msg.get("text") or "").strip().lower()
        if not text.startswith("/"):
            continue

        cmd = text.split()[0]
        if cmd == "/pause":
            state.paused = True
            _send("⏸️ Bot pausado. /resume para reanudar.")
        elif cmd == "/resume":
            state.paused = False
            _send("▶️ Bot reanudado.")
        elif cmd == "/stop":
            state.stop_requested = True
            _send("🛑 Stop solicitado. El bot saldrá tras el ciclo actual.")
        elif cmd == "/status":
            _send(status_snapshot or "ℹ️ Bot vivo. (sin snapshot disponible aún)")
        elif cmd == "/close_all":
            # La acción real la ejecuta el scheduler tras detectar la flag.
            state.paused = True
            state.stop_requested = True
            _send("⚠️ /close_all recibido. Se cancelará OCO y se venderá la posición en el próximo ciclo.")
        elif cmd in ("/help", "/start"):
            _send(HELP)
        else:
            _send(f"❓ Comando desconocido: {cmd}\n{HELP}")

    save(state)
    return state
