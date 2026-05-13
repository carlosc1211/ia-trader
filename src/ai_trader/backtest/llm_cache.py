"""Caché de respuestas del LLM keyed por hash del snapshot.

Permite re-ejecutar backtests sin volver a pagar a Anthropic. Almacena en
SQLite (data/llm_cache.sqlite) con columnas (key, signal_json, meta_json).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from ai_trader.brain.signal import TradingSignal
from ai_trader.config import ROOT


def _db_path() -> Path:
    p = ROOT / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p / "llm_cache.sqlite"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.execute("CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, signal_json TEXT, meta_json TEXT)")
    return c


def snapshot_key(snapshot_dict: dict, model: str, prompt_version: str = "v1") -> str:
    payload = json.dumps({"s": snapshot_dict, "m": model, "p": prompt_version},
                          sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def get(key: str) -> tuple[TradingSignal, dict] | None:
    with _conn() as c:
        row = c.execute("SELECT signal_json, meta_json FROM llm_cache WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return TradingSignal.model_validate_json(row[0]), json.loads(row[1])


def put(key: str, signal: TradingSignal, meta: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO llm_cache(key,signal_json,meta_json) VALUES (?,?,?)",
            (key, signal.model_dump_json(), json.dumps(meta, default=str)),
        )
