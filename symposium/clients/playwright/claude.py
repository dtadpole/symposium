"""Claude web client via Playwright."""

import time
from .base import PlaywrightChatClient


class ClaudeWebClient(PlaywrightChatClient):
    name = "Claude"
    start_url = "https://claude.ai/new"

    def _init_conversation(self):
        self._page.goto(self.start_url, wait_until="domcontentloaded")
        self._page.wait_for_timeout(4000)
        # Wait for the contenteditable input
        self._page.wait_for_selector(
            'div[contenteditable="true"], [data-testid="chat-input"]',
            timeout=20000
        )

    def _type_and_send(self, text: str):
        page = self._page
        # Find contenteditable input
        box = page.locator('div[contenteditable="true"]').first
        box.click()
        box.fill("")
        # Use keyboard to type (contenteditable doesn't support .fill() well)
        page.keyboard.type(text, delay=5)
        page.wait_for_timeout(300)
        # Click send button
        try:
            send = page.locator('button[aria-label="Send message"], button[type="submit"]').last
            send.click(timeout=3000)
        except Exception:
            page.keyboard.press("Enter")

    def _wait_for_response(self) -> str:
        page = self._page
        # Wait for streaming indicator to appear
        try:
            page.wait_for_selector(
                '[data-is-streaming="true"], .streaming-indicator, [aria-label="Stop"]',
                timeout=15000
            )
        except Exception:
            pass
        # Wait for streaming to end
        try:
            page.wait_for_selector(
                '[data-is-streaming="true"]',
                state="hidden",
                timeout=120000
            )
        except Exception:
            pass
        page.wait_for_timeout(1000)

        # Extract last assistant message
        # Claude uses .font-claude-message or similar
        selectors = [
            '[data-testid="assistant-message"]',
            '.font-claude-message',
            '[class*="assistant"]',
        ]
        for sel in selectors:
            msgs = page.locator(sel).all()
            if msgs:
                return msgs[-1].inner_text().strip()

        # Broad fallback
        content = page.locator('article, [role="article"]').all()
        if content:
            return content[-1].inner_text().strip()

        return "[Claude: could not extract response]"
