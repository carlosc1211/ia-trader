"""Contrato de salida del LLM: TradingSignal.

Cualquier respuesta de Claude que no encaje en este modelo se rechaza
antes de tocar el broker. Los validadores aquí cubren la coherencia
estructural (SL/TP en el lado correcto, magnitudes positivas); las
reglas de gestión de riesgo (RR mínimo, % máximo) viven en
ai_trader.brain.risk_validator.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


Action = Literal["long", "short", "flat"]


class TradingSignal(BaseModel):
    action: Action = Field(..., description="Dirección: long, short o flat (sin operación).")
    entry: float = Field(..., gt=0, description="Precio de entrada sugerido.")
    stop_loss: float = Field(..., gt=0, description="Precio de stop loss.")
    take_profit: float = Field(..., gt=0, description="Precio de take profit.")
    size_pct: float = Field(..., ge=0, le=1, description="Fracción del capital a arriesgar (0..1).")
    confidence: float = Field(..., ge=0, le=1, description="Confianza del modelo en la señal (0..1).")
    rationale: str = Field(..., min_length=10, description="Justificación breve de la decisión.")

    @model_validator(mode="after")
    def _check_direction(self) -> "TradingSignal":
        if self.action == "long":
            if not (self.stop_loss < self.entry < self.take_profit):
                raise ValueError("long: se requiere stop_loss < entry < take_profit")
        elif self.action == "short":
            if not (self.take_profit < self.entry < self.stop_loss):
                raise ValueError("short: se requiere take_profit < entry < stop_loss")
        elif self.action == "flat":
            if self.size_pct != 0:
                raise ValueError("flat: size_pct debe ser 0")
        return self

    @property
    def risk_reward(self) -> float:
        if self.action == "long":
            risk = self.entry - self.stop_loss
            reward = self.take_profit - self.entry
        elif self.action == "short":
            risk = self.stop_loss - self.entry
            reward = self.entry - self.take_profit
        else:
            return 0.0
        return reward / risk if risk > 0 else 0.0


SIGNAL_TOOL_SCHEMA = {
    "name": "emit_trading_signal",
    "description": (
        "Emite una señal de trading estructurada. Debes incluir SIEMPRE "
        "stop_loss y take_profit coherentes con la dirección. Si no ves "
        "oportunidad clara, usa action='flat' con size_pct=0 y entry=stop_loss="
        "take_profit=precio_actual."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["long", "short", "flat"]},
            "entry": {"type": "number", "description": "Precio de entrada."},
            "stop_loss": {"type": "number", "description": "Precio de stop loss."},
            "take_profit": {"type": "number", "description": "Precio de take profit."},
            "size_pct": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Fracción de capital (0..1). 0 si flat.",
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "description": "Por qué tomas esta decisión."},
        },
        "required": ["action", "entry", "stop_loss", "take_profit", "size_pct", "confidence", "rationale"],
    },
}
