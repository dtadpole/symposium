"""Multi-layer reply extraction for AI chat UIs.

Strategy
--------
1. scan candidate text blocks before send
2. scan candidate text blocks after response finishes
3. diff: find new / expanded blocks
4. rank candidates with heuristics
5. optionally ask LLM to confirm the best candidate
"""

from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from ...config import get_anthropic_key

MODEL = "claude-sonnet-4-6"

UI_NOISE_RE = re.compile(
    r"^(Share|Copy|Retry|Edit|Regenerate|Show thinking|Hide thinking|Gemini said|"
    r"Claude is AI|Sonnet\b|Opus\b|Haiku\b|ChatGPT\b|PRO\b|Fast\b|Thinking\b)$",
    re.I,
)


# ── scan ──────────────────────────────────────────────────────────────────────
def scan_reply_candidates(page) -> list[dict[str, Any]]:
    """Collect visible text blocks that could be reply content."""
    blocks = page.evaluate(
        r'''() => {
        function isVisible(el) {
          const r = el.getBoundingClientRect();
          const s = window.getComputedStyle(el);
          return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
        }
        function norm(s) {
          return (s || '').toString().replace(/\s+/g, ' ').trim();
        }
        const sels = [
          '[data-testid="assistant-message"]',
          '[data-message-author-role="assistant"]',
          '[data-message-author-role="model"]',
          'model-response',
          'message-content',
          '.markdown',
          '.font-claude-message',
          'article',
          '.response-container',
          '.response-container-content',
          '.model-response-text',
          '.whitespace-pre-wrap',
          'main div',
        ];
        const seen = new Set();
        const out = [];
        for (const sel of sels) {
          document.querySelectorAll(sel).forEach((el) => {
            if (!isVisible(el)) return;
            const text = norm(el.innerText || el.textContent || '');
            if (!text) return;
            if (seen.has(text)) return;
            seen.add(text);
            const r = el.getBoundingClientRect();
            out.push({
              selector: sel,
              text,
              len: text.length,
              x: Math.round(r.x), y: Math.round(r.y),
              w: Math.round(r.width), h: Math.round(r.height),
              tag: el.tagName,
              className: (el.className || '').toString().slice(0,120),
            });
          });
        }
        return out.slice(0, 300);
      }'''
    )
    return [b for b in blocks if _keep_candidate(b)]


# ── heuristics ────────────────────────────────────────────────────────────────
def _keep_candidate(b: dict[str, Any]) -> bool:
    t = (b.get("text") or "").strip()
    if not t:
        return False
    if len(t) < 8:
        return False
    if UI_NOISE_RE.match(t):
        return False
    # drop obvious nav/sidebar snippets
    if len(t) < 40 and b.get("x", 0) < 320:
        return False
    return True


def diff_candidates(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before_texts = {b.get("text", "") for b in before}
    out = []
    for cand in after:
        text = cand.get("text", "")
        if text not in before_texts:
            cand = dict(cand)
            cand["delta"] = "new"
            out.append(cand)
            continue
        # expanded block: same prefix exists in before but now longer
        for old in before:
            old_text = old.get("text", "")
            if old_text and text.startswith(old_text) and len(text) > len(old_text) + 20:
                cand = dict(cand)
                cand["delta"] = f"expanded:+{len(text)-len(old_text)}"
                out.append(cand)
                break
    return out


def rank_candidates(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for c in cands:
        score = 0
        t = c.get("text", "")
        l = c.get("len", len(t))
        y = c.get("y", 9999)
        x = c.get("x", 9999)
        # prefer substantial natural-language blocks
        score += min(l, 1500) / 20
        # prefer main content area over sidebar
        if x > 300:
            score += 20
        # prefer lower blocks (recent replies tend to be lower)
        score += max(0, min(y, 1500) / 80)
        # prefer blocks with punctuation / sentence structure
        if any(p in t for p in ['.', '。', ':', '：', ',', '，', '\n']):
            score += 10
        # prefer explicit diffs
        if c.get("delta") == "new":
            score += 20
        elif str(c.get("delta", "")).startswith("expanded"):
            score += 12
        cc = dict(c)
        cc["score"] = round(score, 2)
        ranked.append(cc)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


# ── llm confirm ───────────────────────────────────────────────────────────────
def _client() -> anthropic.Anthropic | None:
    key = get_anthropic_key()
    return anthropic.Anthropic(api_key=key) if key else None


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def confirm_best_candidate(platform: str, ranked: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not ranked:
        return None
    cli = _client()
    top = ranked[:8]
    if cli:
        payload = {
            "platform": platform,
            "task": "Choose which candidate is the actual latest assistant reply. Prefer newly added main-content reply blocks, not labels/UI.",
            "candidates": [
                {
                    "idx": i,
                    "delta": c.get("delta", ""),
                    "score": c.get("score", 0),
                    "x": c.get("x", 0),
                    "y": c.get("y", 0),
                    "len": c.get("len", 0),
                    "text": c.get("text", "")[:1200],
                }
                for i, c in enumerate(top)
            ],
            "schema": {"idx": 0, "reason": "short explanation"},
        }
        try:
            msg = cli.messages.create(
                model=MODEL,
                max_tokens=250,
                system="Return strict JSON only.",
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            data = _extract_json(msg.content[0].text.strip())
            idx = data.get("idx")
            if isinstance(idx, int) and 0 <= idx < len(top):
                chosen = dict(top[idx])
                chosen["confirm_reason"] = data.get("reason", "")
                chosen["confirm_source"] = "llm"
                return chosen
        except Exception:
            pass
    # fallback to top-ranked
    chosen = dict(top[0])
    chosen["confirm_source"] = "heuristic"
    return chosen


# ── main helper ───────────────────────────────────────────────────────────────
def extract_reply(platform: str, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> str:
    diffs = diff_candidates(before, after)
    pool = diffs if diffs else after
    ranked = rank_candidates(pool)
    best = confirm_best_candidate(platform, ranked)
    if not best:
        return ""
    return (best.get("text") or "").strip()
