"""LLM-assisted chooser for latest model + strongest thinking mode.

Flow:
1. scrape visible menu options from the page
2. ask Claude (API) to choose the newest model and strongest reasoning mode
3. return exact text snippets to click
"""

from __future__ import annotations

import json
import re
from typing import Sequence

import anthropic

from ...config import get_anthropic_key

MODEL = "claude-sonnet-4-6"

SYSTEM = """You help a browser automation system choose the best chat configuration.
Your task: from a list of visible UI labels from one AI chat product, decide:
1. which option most likely corresponds to the newest / strongest model
2. which option most likely corresponds to the deepest / longest reasoning mode

Important principles:
- Prefer the newest version number when visible.
- Prefer stronger tiers like Opus > Sonnet > Haiku when version is comparable.
- Prefer deeper modes like Extended / Extended thinking / Pro / Thinking over Fast / Instant / Auto.
- If the current visible state already looks best, you may choose it.
- Output strict JSON only.
"""


def _client() -> anthropic.Anthropic | None:
    key = get_anthropic_key()
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)


def _extract_json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def choose_best(platform: str, current_label: str, options: Sequence[str]) -> dict:
    """Return dict with target_model, target_mode, rationale.

    Fallbacks to heuristic if API unavailable.
    """
    opts = [o.strip() for o in options if o and o.strip()]
    cli = _client()
    if cli and opts:
        prompt = {
            "platform": platform,
            "current_label": current_label,
            "visible_options": opts,
            "goal": "choose newest model and deepest reasoning mode before sending a message",
            "output_schema": {
                "target_model": "exact text or short distinctive substring from UI",
                "target_mode": "exact text or short distinctive substring from UI",
                "rationale": "short explanation"
            }
        }
        try:
            msg = cli.messages.create(
                model=MODEL,
                max_tokens=250,
                system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False, indent=2)}],
            )
            text = msg.content[0].text.strip()
            data = _extract_json(text)
            if data:
                return {
                    "target_model": (data.get("target_model") or "").strip(),
                    "target_mode": (data.get("target_mode") or "").strip(),
                    "rationale": (data.get("rationale") or "").strip(),
                    "source": "llm",
                }
        except Exception:
            pass

    # Heuristic fallback
    joined = " | ".join(opts)
    # model preference order
    model_candidates = [
        "Opus 5", "Opus 4.7", "Opus 4.6", "Sonnet 5", "Sonnet 4.7", "Sonnet 4.6",
        "5.6", "5.5", "5.4", "3.1 Pro", "3.0", "Pro",
    ]
    mode_candidates = [
        "Extended thinking", "Extended", "Pro", "Thinking", "Reasoning", "Deep Research",
    ]
    target_model = next((x for x in model_candidates if x in joined or x in current_label), current_label)
    target_mode = next((x for x in mode_candidates if x in joined or x in current_label), "")
    return {
        "target_model": target_model,
        "target_mode": target_mode,
        "rationale": "heuristic fallback",
        "source": "heuristic",
    }
