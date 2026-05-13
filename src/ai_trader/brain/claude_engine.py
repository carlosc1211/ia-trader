"""Motor de decisión basado en Claude Sonnet 4.6.

Diseño:
- Tool use obligatorio (tool_choice=any) para forzar el schema de salida.
- Prompt caching en el system prompt: las reglas de trading no cambian
  entre llamadas, así que evitamos pagarlas en cada decisión.
- El snapshot de mercado va como user message (cambia cada vela, no se cachea).
"""
from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic

from ai_trader.brain.signal import SIGNAL_TOOL_SCHEMA, TradingSignal
from ai_trader.config import env


SYSTEM_PROMPT = """Eres un trader cuantitativo conservador operando crypto spot en Binance.

Tu trabajo: a partir de un snapshot multi-timeframe (precio, indicadores técnicos,
niveles S/R, últimas velas) decidir una acción discreta: long, short o flat.

Reglas no negociables:
1. SIEMPRE emites la decisión vía la tool `emit_trading_signal`. Nunca respondas en texto libre.
2. Si la acción es long o short, defines obligatoriamente stop_loss y take_profit coherentes:
   - long: stop_loss < entry < take_profit
   - short: take_profit < entry < stop_loss
3. Risk/reward mínimo objetivo: 1.5. Si no lo ves, prefiere `flat`.
4. `size_pct` ∈ [0, 0.20]. Más exposición que eso está prohibida por el sistema.
5. En duda → `flat` con size_pct=0. No fuerces operaciones.
6. `rationale` debe citar al menos 2 elementos concretos del snapshot (un indicador,
   un nivel, una vela...). Evita generalidades como "el mercado parece alcista".

Sesgo: prefieres no operar a operar mal. Hay 4 decisiones máximas por día; cada una cuenta."""


def _client() -> Anthropic:
    key = env("ANTHROPIC_API_KEY", required=True)
    return Anthropic(api_key=key)


def decide(snapshot_dict: dict, model: str = "claude-sonnet-4-6", max_tokens: int = 1500,
           temperature: float = 0.3) -> tuple[TradingSignal, dict[str, Any]]:
    """Pide una decisión a Claude. Devuelve (signal, raw_response_dict)."""
    client = _client()

    user_content = (
        "Snapshot de mercado actual (JSON):\n\n"
        f"```json\n{json.dumps(snapshot_dict, indent=2, default=str)}\n```\n\n"
        "Emite la señal vía la tool."
    )

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[SIGNAL_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "emit_trading_signal"},
        messages=[{"role": "user", "content": user_content}],
    )

    tool_use = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Claude no devolvió tool_use; respuesta inesperada.")

    signal = TradingSignal.model_validate(tool_use.input)

    meta = {
        "model": resp.model,
        "stop_reason": resp.stop_reason,
        "usage": resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage),
    }
    return signal, meta
