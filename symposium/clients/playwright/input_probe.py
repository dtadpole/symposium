"""Robust input probing for AI chat UIs.

Strategy:
1. collect multiple candidate inputs
2. click each candidate
3. type a short probe token
4. verify the token is visibly present in that candidate / active area
5. delete the probe token
6. return the working selector/strategy
"""

from __future__ import annotations

import time
from typing import Any

PROBE_TEXT = "__BL_INPUT_PROBE__"


def collect_input_candidates(page) -> list[dict[str, Any]]:
    return page.evaluate(
        r'''() => {
        function isVisible(el) {
          const r = el.getBoundingClientRect();
          const s = window.getComputedStyle(el);
          return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
        }
        function short(x) { return (x || '').toString().slice(0, 200); }
        const sels = [
          '#prompt-textarea', '.ql-editor', '.ProseMirror', 'rich-textarea',
          'textarea', '[contenteditable="true"]', 'input[type="text"]'
        ];
        const out = [];
        for (const sel of sels) {
          document.querySelectorAll(sel).forEach((el, idx) => {
            if (!isVisible(el)) return;
            const r = el.getBoundingClientRect();
            out.push({
              selector: sel,
              idx,
              tag: el.tagName,
              id: el.id || '',
              className: short(el.className),
              placeholder: el.getAttribute('placeholder') || el.getAttribute('aria-placeholder') || '',
              contenteditable: el.getAttribute('contenteditable') || '',
              text: short(el.innerText || el.textContent || ''),
              x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)
            });
          });
        }
        return out;
      }'''
    )


def _candidate_locator(page, cand: dict[str, Any]):
    return page.locator(cand["selector"]).nth(int(cand.get("idx", 0)))


def _clear_candidate(page, cand: dict[str, Any]):
    loc = _candidate_locator(page, cand)
    try:
        if cand.get("selector") in ["#prompt-textarea", "textarea", "input[type=\"text\"]"]:
            loc.fill("")
            return
    except Exception:
        pass
    try:
        loc.click(timeout=1000)
        page.keyboard.press("Meta+A")
        page.keyboard.press("Backspace")
    except Exception:
        pass


def probe_input_candidate(page, cand: dict[str, Any]) -> bool:
    loc = _candidate_locator(page, cand)
    try:
        loc.click(timeout=1500)
        page.wait_for_timeout(250)
    except Exception:
        return False

    # type probe
    try:
        # direct fill when supported
        if cand.get("selector") in ["#prompt-textarea", "textarea", "input[type=\"text\"]"]:
            loc.fill(PROBE_TEXT)
        else:
            page.keyboard.type(PROBE_TEXT, delay=5)
        page.wait_for_timeout(300)
    except Exception:
        return False

    # verify probe is actually present in visible active area
    try:
        success = page.evaluate(
            '''(probe) => {
              const ae = document.activeElement;
              const txt = (el) => {
                if (!el) return '';
                return (el.value || el.innerText || el.textContent || '').toString();
              };
              if (txt(ae).includes(probe)) return true;
              // also check within visible candidate-ish editors/textareas
              const els = [...document.querySelectorAll('#prompt-textarea,.ql-editor,.ProseMirror,textarea,[contenteditable="true"],input[type="text"]')];
              return els.some(el => {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return false;
                return txt(el).includes(probe);
              });
            }''',
            PROBE_TEXT,
        )
    except Exception:
        success = False

    # cleanup
    try:
        _clear_candidate(page, cand)
        page.wait_for_timeout(200)
    except Exception:
        pass

    return bool(success)


def find_working_input(page) -> dict[str, Any] | None:
    candidates = collect_input_candidates(page)
    # sort: explicit selectors first, larger boxes earlier
    priority = {
        '#prompt-textarea': 0,
        '.ql-editor': 1,
        '.ProseMirror': 2,
        'rich-textarea': 3,
        'textarea': 4,
        '[contenteditable="true"]': 5,
        'input[type="text"]': 6,
    }
    candidates.sort(key=lambda c: (priority.get(c['selector'], 99), -c.get('w', 0), -c.get('h', 0)))

    for cand in candidates:
        if probe_input_candidate(page, cand):
            return cand
    return None
