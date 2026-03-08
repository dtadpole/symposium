"""Claude web client via Playwright — with Cloudflare/Turnstile handling."""

import time
import random
from .base import PlaywrightChatClient


class ClaudeWebClient(PlaywrightChatClient):
    name = "Claude"
    start_url = "https://claude.ai/new"

    def _human_move(self):
        """Simulate brief human-like mouse movement."""
        page = self._page
        try:
            for _ in range(random.randint(2, 4)):
                x = random.randint(200, 800)
                y = random.randint(200, 600)
                page.mouse.move(x, y, steps=random.randint(5, 15))
                page.wait_for_timeout(random.randint(80, 200))
        except Exception:
            pass

    def _handle_cloudflare(self):
        """Wait for Cloudflare/Turnstile to auto-complete (up to 20s)."""
        page = self._page
        for _ in range(20):
            url = page.url
            title = page.title()
            if 'challenge' in url or 'just a moment' in title.lower() or 'security' in title.lower():
                self._human_move()
                page.wait_for_timeout(1000)
            else:
                return True  # Passed
        return False  # Still blocked

    def _handle_login(self):
        """If redirected to login, attempt Google OAuth."""
        page = self._page
        if '/login' not in page.url:
            return True
        try:
            # Wait for Google button to be enabled
            btn = page.locator('[data-testid="login-with-google"]').first
            btn.wait_for(state='visible', timeout=8000)
            # Human-like pause before clicking
            page.wait_for_timeout(random.randint(500, 1200))
            self._human_move()
            btn.click()
            # Wait for Google OAuth redirect back
            page.wait_for_url('**/claude.ai/**', timeout=20000)
            page.wait_for_timeout(2000)
            return '/login' not in page.url
        except Exception as e:
            return False

    def _init_conversation(self):
        page = self._page

        # Navigate with realistic timing
        page.goto("https://claude.ai", wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(2000, 3500))

        # Handle Cloudflare challenge if present
        self._handle_cloudflare()

        # Handle login if needed
        if '/login' in page.url:
            self._handle_login()
            page.wait_for_timeout(2000)

        # Navigate to new chat
        page.goto(self.start_url, wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(2000, 3000))
        self._handle_cloudflare()

        # Wait for input
        page.wait_for_selector(
            'div[contenteditable="true"], [data-testid="chat-input"], .ProseMirror',
            timeout=20000
        )

    def _type_and_send(self, text: str):
        page = self._page

        # Find input
        for sel in ['div[contenteditable="true"]', '.ProseMirror', '[data-testid="chat-input"]']:
            try:
                box = page.locator(sel).first
                if box.is_visible(timeout=2000):
                    box.click()
                    page.wait_for_timeout(random.randint(200, 500))
                    # Type with human-like speed
                    page.keyboard.type(text, delay=random.randint(15, 40))
                    page.wait_for_timeout(random.randint(300, 600))
                    break
            except Exception:
                continue

        # Send
        try:
            send = page.locator('button[aria-label="Send message"]').last
            send.click(timeout=3000)
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

    def _wait_for_response(self) -> str:
        page = self._page
        # Wait for streaming to start
        try:
            page.wait_for_selector('[data-is-streaming="true"]', timeout=15000)
        except Exception:
            pass
        # Wait for streaming to end
        try:
            page.wait_for_selector('[data-is-streaming="true"]', state='hidden', timeout=120000)
        except Exception:
            pass
        page.wait_for_timeout(1000)

        for sel in [
            '[data-testid="assistant-message"]',
            '.font-claude-message',
            'article',
            '[class*="assistant"]',
        ]:
            msgs = page.locator(sel).all()
            if msgs:
                return msgs[-1].inner_text().strip()

        return "[Claude: could not extract response]"
