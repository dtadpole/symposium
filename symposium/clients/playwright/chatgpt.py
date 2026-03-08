"""ChatGPT web client via Playwright."""

import time
from .base import PlaywrightChatClient


class ChatGPTClient(PlaywrightChatClient):
    name = "GPT"
    start_url = "https://chatgpt.com/"

    def _init_conversation(self):
        # Retry up to 3 times in case of navigation abort
        for attempt in range(3):
            try:
                self._page.goto(self.start_url, wait_until="domcontentloaded", timeout=20000)
                self._page.wait_for_timeout(3000)
                break
            except Exception as e:
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
        # Wait for "Stop" button to appear (streaming started)
        try:
            page.wait_for_selector('[data-testid="stop-button"]', timeout=15000)
        except Exception:
            pass
        # Wait for "Stop" to disappear (streaming done)
        try:
            page.wait_for_selector('[data-testid="stop-button"]', state="hidden", timeout=120000)
        except Exception:
            pass
        page.wait_for_timeout(800)

        # Extract the last assistant message
        msgs = page.locator('[data-message-author-role="assistant"]').all()
        if msgs:
            return msgs[-1].inner_text().strip()

        # Fallback: any .markdown block
        blocks = page.locator(".markdown").all()
        if blocks:
            return blocks[-1].inner_text().strip()

        return "[ChatGPT: could not extract response]"
