"""SQLite engine y sesión para ai-trader.

Por defecto: archivo `data/ai_trader.sqlite` en la raíz del proyecto.
Se puede sobrescribir con AI_TRADER_DB_URL (ej. para tests con :memory:).
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ai_trader.config import ROOT, env


def _default_db_url() -> str:
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'ai_trader.sqlite').as_posix()}"


DB_URL = env("AI_TRADER_DB_URL", _default_db_url())

engine = create_engine(DB_URL, future=True, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session() -> Session:
    return SessionLocal()


def init_db() -> None:
    from ai_trader.storage.models import Base
    Base.metadata.create_all(engine)
