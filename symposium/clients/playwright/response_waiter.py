"""Response waiter: observe page state machine + extract reply from anchor.

Flow
----
1. snapshot page state just before send (baseline)
2. send message
3. poll state: sending → thinking → stable
4. detect stuck (2-5 min no change) and hard timeout (15 min)
5. once stable, locate user message anchor
6. extract candidate blocks AFTER anchor
7. rank + LLM confirm which block is the main reply
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import anthropic

MODEL = "claude-sonnet-4-6"

STUCK_TIMEOUT_S = 120     # 2 min no change → suspected stuck
REFRESH_TIMEOUT_S = 300   # 5 min → try refresh
HARD_TIMEOUT_S = 900      # 15 min → give up

UI_NOISE = re.compile(
    r"^(Share|Copy|Retry|Edit|Regenerate|Show thinking|Hide thinking|Gemini said|"
    r"Claude is AI|Sonnet|Opus|Haiku|ChatGPT|PRO|Fast|Thinking|Stop|Send|New chat|"
    r"Search|More models|Customize|Projects|Artifacts|Recents|All chats)$",
    re.I,
)


# ─── state observers ──────────────────────────────────────────────────────────

# platform-specific hints for state detection
PLATFORM_HINTS = {
    "Claude": {
        "stop_sels": ['[aria-label="Stop"]', 'button:has-text("Stop")'],
        "thinking_sels": ['[data-is-streaming="true"]'],
        "send_sels": ['button[aria-label="Send message"]', '[data-testid="send-button"]'],
        # Confirmed selectors (inspected live DOM 2026-03-08)
        "done_sels": [
            'button[aria-label="Give positive feedback"]',
            'button[aria-label="Give negative feedback"]',
            '[data-is-streaming="false"]',
        ],
    },
    "ChatGPT": {
        "stop_sels": ['[data-testid="stop-button"]', 'button[aria-label="Stop streaming"]'],
        "thinking_sels": ['[data-testid="stop-button"]'],
        "send_sels": ['[data-testid="send-button"]', 'button[aria-label="Send prompt"]'],
        # NOTE: do NOT include 'button[aria-label="Copy"]' here —
        # that button persists on ALL previous messages and fires false positives.
        "done_sels": [
            'button[aria-label="Good response"]',
            'button[aria-label="Bad response"]',
            '[data-testid="good-response-turn-action-button"]',
            '[data-testid="bad-response-turn-action-button"]',
        ],
    },
    "Gemini": {
        "stop_sels": ['button[aria-label="Stop response"]', 'button[mattooltip="Stop response"]'],
        "thinking_sels": ['.loading-indicator', '[aria-label="Gemini is thinking"]'],
        "send_sels": ['button[aria-label="Send message"]', 'button[mattooltip="Send message"]'],
        "done_sels": [
            'button[aria-label="Good response"]',
            'button[aria-label="Bad response"]',
            'button[mattooltip="Good response"]',
            'button[mattooltip="Bad response"]',
            'button[aria-label="Thumb up"]',
            'button[aria-label="Thumb down"]',
            'button[aria-label="Copy"]',
        ],
    },
}


def _el_exists(page, sels: list[str]) -> bool:
    for sel in sels:
        try:
            if page.locator(sel).count() > 0 and page.locator(sel).first.is_visible(timeout=300):
                return True
        except Exception:
            pass
    return False


def _page_state_snapshot(page, platform: str) -> dict[str, Any]:
    hints = PLATFORM_HINTS.get(platform, PLATFORM_HINTS["Claude"])
    stop_visible = _el_exists(page, hints["stop_sels"])
    thinking_visible = _el_exists(page, hints["thinking_sels"])
    send_visible = _el_exists(page, hints["send_sels"])
    # Feedback/rating buttons are the strongest "done" signal
    done_visible = _el_exists(page, hints.get("done_sels", []))

    try:
        text_len = page.evaluate(
            '''() => {
              const main = document.querySelector('main') || document.body;
              return (main.innerText || '').length;
            }'''
        )
    except Exception:
        text_len = 0

    return {
        "ts": time.time(),
        "stop_visible": stop_visible,
        "thinking_visible": thinking_visible,
        "send_visible": send_visible,
        "done_visible": done_visible,
        "text_len": text_len,
    }


def _state_changed(a: dict, b: dict) -> bool:
    return (
        a.get("stop_visible") != b.get("stop_visible") or
        a.get("thinking_visible") != b.get("thinking_visible") or
        a.get("send_visible") != b.get("send_visible") or
        a.get("done_visible") != b.get("done_visible") or
        abs((a.get("text_len") or 0) - (b.get("text_len") or 0)) > 30
    )


def _is_stable(snap: dict) -> bool:
    """Page is done when:
    1. Feedback/rating buttons appeared (strongest signal), OR
    2. Send button visible + stop/thinking gone
    """
    # Primary: feedback buttons appeared = output definitely finished
    if snap.get("done_visible"):
        return True
    # Secondary: send restored, stop gone, thinking gone
    return (
        snap.get("send_visible", False) and
        not snap.get("stop_visible", False) and
        not snap.get("thinking_visible", False)
    )


def _scroll_to_bottom(page):
    """Scroll ALL scrollable conversation containers to bottom."""
    try:
        page.evaluate(
            '''() => {
              // Find all large scrollable containers and scroll them all
              const candidates = [...document.querySelectorAll('*')].filter(el => {
                const s = window.getComputedStyle(el);
                const ov = s.overflowY;
                if (ov !== 'auto' && ov !== 'scroll') return false;
                const r = el.getBoundingClientRect();
                return r.width > 200 && r.height > 200 && el.scrollHeight > el.clientHeight;
              });
              candidates.forEach(el => {
                el.scrollTop = el.scrollHeight;
              });
              // Also scroll window
              window.scrollTo(0, document.body.scrollHeight);
            }'''
        )
        page.wait_for_timeout(500)
    except Exception:
        pass


def check_done(page, platform: str) -> bool:
    """Non-blocking check: are feedback buttons visible? (= AI finished responding)"""
    try:
        _scroll_to_bottom(page)
        snap = _page_state_snapshot(page, platform)
        return _is_stable(snap)
    except Exception:
        return False


def wait_for_completion(page, platform: str, baseline_snap: dict) -> str:
    """
    Poll page state until stable, stuck, or timeout.
    Returns: 'stable' | 'stuck' | 'timeout' | 'refreshed'
    """
    start = time.time()
    last_change_ts = start
    last_snap = baseline_snap

    # Give a moment for the stop button to appear
    page.wait_for_timeout(1500)

    scroll_interval = 0  # scroll every ~10 polls = ~15s
    while True:
        elapsed = time.time() - start

        # Scroll to bottom every ~15s so long responses + feedback buttons are visible
        scroll_interval += 1
        if scroll_interval % 10 == 0:
            _scroll_to_bottom(page)

        snap = _page_state_snapshot(page, platform)

        if _state_changed(last_snap, snap):
            last_change_ts = time.time()
            last_snap = snap

        if _is_stable(snap) and elapsed > 2:
            # One final scroll to make sure everything is rendered
            _scroll_to_bottom(page)
            page.wait_for_timeout(400)
            return "stable"

        since_change = time.time() - last_change_ts

        if elapsed > HARD_TIMEOUT_S:
            _scroll_to_bottom(page)
            return "timeout"

        if since_change > REFRESH_TIMEOUT_S:
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            except Exception:
                pass
            return "refreshed"

        if since_change > STUCK_TIMEOUT_S and elapsed > STUCK_TIMEOUT_S:
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            except Exception:
                pass
            return "refreshed"

        page.wait_for_timeout(1500)


# ─── anchor + reply extraction ────────────────────────────────────────────────

def _scan_message_blocks(page) -> list[dict[str, Any]]:
    """Scan all visible message blocks in the conversation flow."""
    return page.evaluate(
        r'''() => {
          function norm(s) { return (s||'').replace(/\s+/g,' ').trim(); }
          const sels = [
            '[data-is-streaming]',
            '[data-message-author-role]',
            '[data-testid*="message"]',
            'article',
            'model-response',
            'user-query',
            '[class*="message"][class*="user"]',
            '[class*="message"][class*="assistant"]',
            '[class*="ConversationItem"]',
          ];
          const seen = new Set();
          const out = [];
          for (const sel of sels) {
            document.querySelectorAll(sel).forEach((el, idx) => {
              const r = el.getBoundingClientRect();
              if (r.width <= 0 || r.height <= 0) return;
              const text = norm(el.innerText || el.textContent || '');
              if (!text || text.length < 5) return;
              const key = sel + '|' + idx;
              if (seen.has(key)) return;
              seen.add(key);
              const role = el.getAttribute('data-message-author-role') || '';
              out.push({
                sel, idx,
                role,
                text,
                len: text.length,
                y: Math.round(r.y),
                h: Math.round(r.h || r.height),
              });
            });
          }
          // sort by vertical position
          out.sort((a,b) => a.y - b.y);
          return out;
        }'''
    )


def _find_anchor(blocks: list[dict], prompt: str) -> int:
    """Find index of user message block containing our prompt (or close to it)."""
    prompt_short = prompt[:120].strip()
    # exact match first
    for i, b in enumerate(blocks):
        if prompt_short in b.get("text", ""):
            return i
    # partial match
    words = prompt_short.split()[:8]
    for i, b in enumerate(blocks):
        t = b.get("text", "")
        if sum(1 for w in words if w in t) >= min(5, len(words)):
            return i
    return -1


def _is_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if len(t) < 10:
        return True
    if UI_NOISE.match(t):
        return True
    return False


def _get_anthropic_key() -> str | None:
    import json as _json
    from pathlib import Path
    p = Path.home() / ".openclaw/agents/main/agent/auth-profiles.json"
    try:
        data = _json.loads(p.read_text())
        return data["profiles"]["anthropic:default"]["token"]
    except Exception:
        return None


def _llm_clean_reply(platform: str, prompt: str, raw_content: str) -> str:
    """Given noisy raw content scraped after the user message, ask LLM to extract the clean reply."""
    key = _get_anthropic_key()
    if not key:
        return raw_content

    cli = anthropic.Anthropic(api_key=key)
    system = (
        "You are extracting the AI assistant's reply from raw scraped page content. "
        "The page content may contain noise: UI labels, model names, thinking traces, "
        "old history, button text, etc. "
        "Return ONLY the clean main reply text. No explanation, no quotes, no JSON wrapper."
    )
    user_msg = (
        f"Platform: {platform}\n"
        f"User sent: {prompt[:300]}\n\n"
        f"Raw scraped content after user message:\n{raw_content[:4000]}\n\n"
        "Extract ONLY the clean assistant reply to the user's message. "
        "If there are multiple candidates, pick the main substantive reply. "
        "Do not include thinking traces, UI text, or old history."
    )
    try:
        msg = cli.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        result = msg.content[0].text.strip()
        if result:
            return result
    except Exception:
        pass
    return raw_content


def _llm_pick_reply(platform: str, candidates: list[dict]) -> str:
    valid = [c for c in candidates if not _is_noise(c.get("text", ""))]
    if not valid:
        return ""
    # just return the longest — LLM will clean it later
    return max(valid, key=lambda x: x.get("len", 0)).get("text", "")


def extract_reply_after_anchor(page, platform: str, prompt: str) -> str:
    """Main entry: find user message anchor, collect blocks after it, LLM clean reply."""
    # Scroll to bottom first so long responses are fully in DOM
    _scroll_to_bottom(page)
    page.wait_for_timeout(500)
    blocks = _scan_message_blocks(page)
    anchor_idx = _find_anchor(blocks, prompt)

    if anchor_idx >= 0:
        after = blocks[anchor_idx + 1:]
    else:
        # fallback: take last 8 blocks
        after = blocks[-8:]

    if not after:
        return ""

    # Concatenate all post-anchor content (noise is OK — LLM will clean it)
    raw = "\n\n---\n\n".join(b.get("text", "") for b in after if b.get("text"))
    if not raw.strip():
        return ""

    # LLM cleans the noise and extracts the actual reply
    return _llm_clean_reply(platform, prompt, raw)
