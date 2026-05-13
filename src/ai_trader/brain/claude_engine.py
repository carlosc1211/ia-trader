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

from ai_trader.brain.prompts import get as get_prompt
from ai_trader.brain.signal import SIGNAL_TOOL_SCHEMA, TradingSignal
from ai_trader.config import env


DEFAULT_PROMPT_VERSION = "v2"


def _client() -> Anthropic:
    key = env("ANTHROPIC_API_KEY", required=True)
    return Anthropic(api_key=key)


def decide(snapshot_dict: dict, model: str = "claude-sonnet-4-6", max_tokens: int = 1500,
           temperature: float = 0.3, prompt_version: str = DEFAULT_PROMPT_VERSION,
           ) -> tuple[TradingSignal, dict[str, Any]]:
    """Pide una decisión a Claude. Devuelve (signal, raw_response_dict)."""
    client = _client()
    system_prompt = get_prompt(prompt_version)

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
                "text": system_prompt,
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
        "prompt_version": prompt_version,
    }
    return signal, meta
