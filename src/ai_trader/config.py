from pathlib import Path
import os
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def env(key: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(key, default)
    if required and not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def load_yaml(path: str | Path = "config.yaml") -> dict:
    p = ROOT / path if not Path(path).is_absolute() else Path(path)
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


MODE = env("AI_TRADER_MODE", "paper")
LOG_DIR = Path(env("AI_TRADER_LOG_DIR", str(ROOT / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_LEVEL = env("AI_TRADER_LOG_LEVEL", "INFO")
