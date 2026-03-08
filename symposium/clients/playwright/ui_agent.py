"""LLM-guided UI scanning / action planning / recovery helpers.

This module upgrades browser clients from fixed selectors to:
1. scan visible UI candidates
2. ask LLM which element/action to use
3. execute action
4. recover page back to usable input state if needed
"""

from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from ...config import get_anthropic_key

MODEL = "claude-sonnet-4-6"

_SYSTEM = """You are helping a browser automation agent operate AI chat UIs.
Given a list of visible UI candidates, choose the best next action.
Return strict JSON only.
Rules:
- Prefer newest / strongest model.
- Prefer deepest thinking mode.
- Prefer primary composer input over search boxes / sidebar filters.
- If a menu/modal is open and input is blocked, choose a recovery action first.
- Use text snippets exactly from candidates when possible.
"""


def _client() -> anthropic.Anthropic | None:
    key = get_anthropic_key()
    return anthropic.Anthropic(api_key=key) if key else None


def scan_ui(page) -> dict[str, Any]:
    return page.evaluate(
        r'''() => {
        function isVisible(el) {
          const r = el.getBoundingClientRect();
          const s = window.getComputedStyle(el);
          return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
        }
        function txt(el) {
          return ((el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim()).replace(/\s+/g, ' ').slice(0, 180);
        }
        const buttons = [...document.querySelectorAll('button,[role="button"],[role="menuitem"],[role="option"],[aria-haspopup],[role="combobox"]')]
          .filter(isVisible)
          .map((el, i) => ({idx:i, text:txt(el), tag:el.tagName, role:el.getAttribute('role'), testid:el.getAttribute('data-testid')}))
          .filter(x => x.text);
        const inputs = [...document.querySelectorAll('textarea,input,[contenteditable="true"],.ql-editor,.ProseMirror,#prompt-textarea,rich-textarea')]
          .filter(isVisible)
          .map((el, i) => ({idx:i, text:txt(el), tag:el.tagName, type:el.getAttribute('type'), placeholder:el.getAttribute('placeholder')||el.getAttribute('aria-placeholder')||'', contenteditable:el.getAttribute('contenteditable'), id:el.id||'', className:(el.className||'').toString().slice(0,120)}));
        const overlays = [...document.querySelectorAll('[role="dialog"],[aria-modal="true"],[data-state="open"],menu,dialog')]
          .filter(isVisible)
          .map((el, i) => ({idx:i, text:txt(el), tag:el.tagName, role:el.getAttribute('role')}));
        return {
          title: document.title,
          url: location.href,
          buttons: buttons.slice(0, 120),
          inputs: inputs.slice(0, 40),
          overlays: overlays.slice(0, 20),
        };
      }'''
    )


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def choose_action(kind: str, platform: str, ui: dict[str, Any], goal: str) -> dict[str, Any]:
    cli = _client()
    payload = {
        "kind": kind,
        "platform": platform,
        "goal": goal,
        "ui": ui,
        "schema": {
            "action": "click_button | focus_input | recover | none",
            "target_text": "exact text or short substring from candidate",
            "reason": "short explanation",
            "fallback_text": "optional second choice",
        },
    }
    if cli:
        try:
            msg = cli.messages.create(
                model=MODEL,
                max_tokens=300,
                system=_SYSTEM,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
            data = _extract_json(msg.content[0].text.strip())
            if data:
                return data
        except Exception:
            pass

    # heuristic fallback
    if kind == "input":
        for cand in ui.get("inputs", []):
            ident = " ".join([cand.get("id", ""), cand.get("className", ""), cand.get("placeholder", ""), cand.get("text", "")])
            if any(x in ident.lower() for x in ["prompt", "composer", "editor", "type", "message", "ql-editor", "prosemirror"]):
                return {"action": "focus_input", "target_text": cand.get("placeholder") or cand.get("id") or cand.get("className") or cand.get("text"), "reason": "heuristic input", "fallback_text": ""}
        if ui.get("inputs"):
            c = ui["inputs"][0]
            return {"action": "focus_input", "target_text": c.get("placeholder") or c.get("id") or c.get("className") or c.get("text"), "reason": "fallback first input", "fallback_text": ""}
    if kind == "recover":
        return {"action": "recover", "target_text": "", "reason": "close menus and refocus", "fallback_text": ""}
    return {"action": "none", "target_text": "", "reason": "no choice", "fallback_text": ""}


def click_best_button(page, target_text: str, fallback_text: str = "") -> bool:
    for text in [target_text, fallback_text]:
        if not text:
            continue
        candidates = [
            f'button:has-text("{text}")',
            f'[role="button"]:has-text("{text}")',
            f'[role="menuitem"]:has-text("{text}")',
            f'[role="option"]:has-text("{text}")',
            f'text="{text}"',
        ]
        for sel in candidates:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=600):
                    el.click(timeout=2000)
                    page.wait_for_timeout(600)
                    return True
            except Exception:
                pass
    return False


def focus_best_input(page, ui: dict[str, Any]) -> bool:
    selectors = [
        '#prompt-textarea', '.ql-editor', '.ProseMirror',
        'textarea', '[contenteditable="true"]', 'input[type="text"]', 'rich-textarea'
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                el.click(timeout=1500)
                page.wait_for_timeout(300)
                return True
        except Exception:
            pass
    return False


def recover_page(page, ui: dict[str, Any] | None = None) -> bool:
    try:
        page.keyboard.press('Escape')
    except Exception:
        pass
    try:
        page.mouse.click(40, 40)
    except Exception:
        pass
    page.wait_for_timeout(400)
    if ui is None:
        ui = scan_ui(page)
    return focus_best_input(page, ui)
