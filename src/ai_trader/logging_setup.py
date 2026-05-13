"""Logging estructurado a archivo + consola.

Un único logger raíz para todo el proyecto. Los mensajes van a stdout y a
un archivo rotativo en LOG_DIR (configurable vía AI_TRADER_LOG_DIR).
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from ai_trader.config import LOG_DIR, LOG_LEVEL


_CONFIGURED = False


def setup() -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger("ai_trader")
    if _CONFIGURED:
        return root

    root.setLevel(LOG_LEVEL)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "ai_trader.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    root.propagate = False
    _CONFIGURED = True
    return root
