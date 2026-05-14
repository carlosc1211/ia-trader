"""Validación de gestión de riesgo sobre una TradingSignal.

Separado del modelo pydantic para mantener las reglas configurables
(viven en config.yaml) y poder rechazar señales sin tocar el LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai_trader.brain.signal import TradingSignal


@dataclass
class RiskCheck:
    ok: bool
    reasons: list[str]


def validate(signal: TradingSignal, risk_cfg: dict) -> RiskCheck:
    reasons: list[str] = []

    if signal.action == "flat":
        return RiskCheck(ok=True, reasons=[])

    max_pos = float(risk_cfg.get("max_position_pct", 0.2))
    if signal.size_pct > max_pos:
        reasons.append(f"size_pct {signal.size_pct:.3f} > max_position_pct {max_pos:.3f}")

    min_rr = float(risk_cfg.get("min_rr_ratio", 1.5))
    # Tolerancia de 0.005 para absorber redondeos cuando Claude apunta justo al mínimo.
    if signal.risk_reward + 0.005 < min_rr:
        reasons.append(f"RR {signal.risk_reward:.2f} < min_rr_ratio {min_rr:.2f}")

    return RiskCheck(ok=not reasons, reasons=reasons)
