"""Gemini web client via Playwright."""

import time
from .base import PlaywrightChatClient


class GeminiWebClient(PlaywrightChatClient):
    name = "Gemini"
    start_url = "https://gemini.google.com/app"

    def _init_conversation(self):
        self._page.goto(self.start_url, wait_until="domcontentloaded")
        self._page.wait_for_timeout(4000)
        # Wait for the rich-textarea or ql-editor
        self._page.wait_for_selector(
            'rich-textarea, .ql-editor, textarea[placeholder], [contenteditable="true"]',
            timeout=20000
        )

    def _type_and_send(self, text: str):
        page = self._page
        # Try rich-textarea first (Gemini's custom component)
        try:
            box = page.locator('.ql-editor').first
            box.click()
            box.fill("")
            page.keyboard.type(text, delay=5)
        except Exception:
            # Fallback to any contenteditable
            box = page.locator('[contenteditable="true"]').first
            box.click()
            page.keyboard.type(text, delay=5)

        page.wait_for_timeout(300)
        # Click send button
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
        # Wait for loading/streaming indicator
        try:
            page.wait_for_selector(
                '.loading-indicator, [aria-label="Gemini is thinking"], '
                'model-response [class*="loading"], .response-loading',
                timeout=15000
            )
        except Exception:
            pass
        # Wait for it to disappear
        try:
            page.wait_for_selector(
                '.loading-indicator, [aria-label="Gemini is thinking"], '
                '.response-loading',
                state="hidden",
                timeout=120000
            )
        except Exception:
            page.wait_for_timeout(5000)  # fallback wait

        page.wait_for_timeout(1000)

        # Extract last model response
        selectors = [
            'model-response .response-container-content',
            '.model-response-text',
            'message-content',
            '[data-message-author-role="model"]',
        ]
        for sel in selectors:
            msgs = page.locator(sel).all()
            if msgs:
                return msgs[-1].inner_text().strip()

        # Broad fallback
        msgs = page.locator('model-response').all()
        if msgs:
            return msgs[-1].inner_text().strip()

        return "[Gemini: could not extract response]"
