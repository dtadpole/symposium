"""Gemini web client via Playwright."""

import time
from .base import PlaywrightChatClient
from .chooser import choose_best


class GeminiWebClient(PlaywrightChatClient):
    name = "Gemini"
    start_url = "https://gemini.google.com/app"

    def ensure_best_config(self):
        page = self._page
        try:
            current = ""
            menu_btn = None
            for sel in ['button:has-text("Fast")', 'button:has-text("Thinking")', 'button:has-text("Pro")']:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=1000):
                        menu_btn = el
                        current = el.inner_text(timeout=800).strip()
                        break
                except Exception:
                    pass
            if menu_btn is None:
                return

            menu_btn.click(timeout=2500)
            page.wait_for_timeout(1200)
            options = page.evaluate('''() => [...document.querySelectorAll('[role="menuitem"],button,[role="option"]')]
                .map(el => (el.innerText||el.textContent||'').trim())
                .filter(Boolean)
                .filter(t => t.length < 120)
                .slice(0,140)''')
            choice = choose_best('Gemini', current, options)

            for target in [choice.get('target_model',''), choice.get('target_mode','')]:
                if not target:
                    continue
                try:
                    el = page.locator(f'button:has-text("{target}")').first
                    if el.is_visible(timeout=1000):
                        el.click(timeout=1800)
                        page.wait_for_timeout(700)
                        break
                except Exception:
                    pass
        except Exception:
            pass

    def _init_conversation(self):
        self._page.goto(self.start_url, wait_until="domcontentloaded")
        self._page.wait_for_timeout(4000)
        self._page.wait_for_selector(
            'rich-textarea, .ql-editor, textarea[placeholder], [contenteditable="true"]',
            timeout=20000
        )

    def _type_and_send(self, text: str):
        page = self._page
        try:
            box = page.locator('.ql-editor').first
            box.click()
            box.fill("")
            page.keyboard.type(text, delay=5)
        except Exception:
            box = page.locator('[contenteditable="true"]').first
            box.click()
            page.keyboard.type(text, delay=5)

        page.wait_for_timeout(300)
        try:
            send = page.locator(
                'button[aria-label="Send message"], button[mattooltip="Send message"], '
                'button.send-button'
            ).last
            send.click(timeout=3000)
        except Exception:
            page.keyboard.press("Enter")

    def _wait_for_response(self) -> str:
        page = self._page
        try:
            page.wait_for_selector(
                '.loading-indicator, [aria-label="Gemini is thinking"], '
                'model-response [class*="loading"], .response-loading',
                timeout=15000
            )
        except Exception:
            pass
        try:
            page.wait_for_selector(
                '.loading-indicator, [aria-label="Gemini is thinking"], .response-loading',
                state="hidden",
                timeout=120000
            )
        except Exception:
            page.wait_for_timeout(5000)

        page.wait_for_timeout(1000)

        for sel in [
            'model-response .response-container-content',
            '.model-response-text',
            'message-content',
            '[data-message-author-role="model"]',
        ]:
            msgs = page.locator(sel).all()
            if msgs:
                return msgs[-1].inner_text().strip()

        msgs = page.locator('model-response').all()
        if msgs:
            return msgs[-1].inner_text().strip()

        return "[Gemini: could not extract response]"
