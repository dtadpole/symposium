"""ChatGPT web client via Playwright."""

import time
from .base import PlaywrightChatClient
from .chooser import choose_best


class ChatGPTClient(PlaywrightChatClient):
    name = "GPT"
    start_url = "https://chatgpt.com/"

    def ensure_best_config(self):
        page = self._page
        try:
            model_btn = page.locator('[data-testid="model-switcher-dropdown-button"]').first
            current = model_btn.inner_text(timeout=2000).strip()
            model_btn.click(timeout=3000)
            page.wait_for_timeout(1200)
            options = page.evaluate('''() => [...document.querySelectorAll('[role="menuitem"],button,[role="option"]')]
                .map(el => (el.innerText||el.textContent||'').trim())
                .filter(Boolean)
                .filter(t => t.length < 120)
                .slice(0,140)''')
            choice = choose_best('ChatGPT', current, options)

            # Pick chosen model
            target_model = choice.get('target_model', '')
            if target_model:
                try:
                    el = page.locator(f'button:has-text("{target_model}")').first
                    if el.is_visible(timeout=1000):
                        el.click(timeout=2000)
                        page.wait_for_timeout(700)
                except Exception:
                    pass

            # Ensure strongest thinking mode
            mode_candidates = [x for x in [choice.get('target_mode', ''), 'Extended thinking', 'Thinking', 'Pro'] if x]
            for target_mode in mode_candidates:
                try:
                    el = page.locator(f'button:has-text("{target_mode}")').last
                    if el.is_visible(timeout=800):
                        el.click(timeout=1800)
                        page.wait_for_timeout(600)
                        break
                except Exception:
                    pass
        except Exception:
            pass

    def _init_conversation(self):
        # Retry up to 3 times in case of navigation abort
        for attempt in range(3):
            try:
                self._page.goto(self.start_url, wait_until="domcontentloaded", timeout=20000)
                self._page.wait_for_timeout(3000)
                break
            except Exception:
                if attempt == 2:
                    raise
                self._page.wait_for_timeout(2000)

        # Click "New Chat" if present
        try:
            new_chat = self._page.locator('[data-testid="create-new-chat-button"], a[href="/"]').first
            if new_chat.is_visible(timeout=2000):
                new_chat.click()
                self._page.wait_for_timeout(1500)
        except Exception:
            pass
        # Wait for input to appear
        self._page.wait_for_selector("#prompt-textarea", timeout=20000)

    def _type_and_send(self, text: str):
        box = self._page.locator("#prompt-textarea").first
        box.click()
        box.fill("")
        box.type(text, delay=10)
        self._page.wait_for_timeout(300)
        # Press Enter or click send button
        try:
            send = self._page.locator('[data-testid="send-button"]').first
            send.click(timeout=3000)
        except Exception:
            box.press("Enter")

    def _wait_for_response(self) -> str:
        page = self._page
        try:
            page.wait_for_selector('[data-testid="stop-button"]', timeout=15000)
        except Exception:
            pass
        try:
            page.wait_for_selector('[data-testid="stop-button"]', state="hidden", timeout=120000)
        except Exception:
            pass
        page.wait_for_timeout(800)

        msgs = page.locator('[data-message-author-role="assistant"]').all()
        if msgs:
            return msgs[-1].inner_text().strip()

        blocks = page.locator('.markdown').all()
        if blocks:
            return blocks[-1].inner_text().strip()

        return "[ChatGPT: could not extract response]"
