"""ChatGPT web client via Playwright."""

import tempfile
import time
from pathlib import Path
from .base import PlaywrightChatClient
from .chooser import choose_best
from .reply_extractor import scan_reply_candidates, extract_reply
from .response_waiter import _page_state_snapshot, wait_for_completion, extract_reply_after_anchor

# Marker used to split attachment content from main prompt
ATTACHMENT_MARKER = "<<<ATTACHMENT>>>"


class ChatGPTClient(PlaywrightChatClient):
    name = "ChatGPT"
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

            target_model = choice.get('target_model', '')
            if target_model:
                try:
                    el = page.locator(f'button:has-text("{target_model}")').first
                    if el.is_visible(timeout=1000):
                        el.click(timeout=2000)
                        page.wait_for_timeout(700)
                except Exception:
                    pass

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
        for attempt in range(3):
            try:
                self._page.goto(self.start_url, wait_until="domcontentloaded", timeout=20000)
                self._page.wait_for_timeout(3000)
                break
            except Exception:
                if attempt == 2:
                    raise
                self._page.wait_for_timeout(2000)

        try:
            new_chat = self._page.locator('[data-testid="create-new-chat-button"], a[href="/"]').first
            if new_chat.is_visible(timeout=2000):
                new_chat.click()
                self._page.wait_for_timeout(1500)
        except Exception:
            pass
        self._page.wait_for_selector("#prompt-textarea", timeout=20000)

    def _upload_file(self, content: str, filename: str = "opponent_argument.txt") -> bool:
        """Upload content as a file attachment to ChatGPT. Returns True if successful.

        Tested approach (confirmed working 2026-03-08):
          1. Write content to temp .txt file
          2. Find hidden file input and call set_input_files() directly
          3. Wait for attachment icon to appear
        """
        page = self._page
        try:
            # Write content to a temp file
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', prefix='symposium_',
                delete=False, encoding='utf-8'
            )
            tmp.write(content)
            tmp.flush()
            tmp_path = tmp.name
            tmp.close()

            # Primary: set_input_files on hidden file input (confirmed working)
            file_input = page.locator('input[type="file"]').first
            if file_input.count() > 0:
                file_input.set_input_files(tmp_path)
            else:
                # Fallback: find upload button and use file chooser
                upload_btn = None
                for sel in [
                    'button[aria-label*="ile"]',       # "Attach files" / "附加文件"
                    'button[aria-label*="ttach"]',
                    'button[aria-label="Attach files"]',
                    'button[aria-label="附加文件"]',
                    'button[data-testid="composer-attachment-button"]',
                ]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1000):
                            upload_btn = el
                            break
                    except Exception:
                        pass

                if upload_btn is None:
                    return False

                with page.expect_file_chooser(timeout=5000) as fc_info:
                    upload_btn.click()
                fc_info.value.set_files(tmp_path)

            # Wait for attachment icon to appear
            page.wait_for_timeout(2500)

            # Clean up temp file
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

            return True
        except Exception as e:
            return False

    def _type_and_send(self, text: str):
        """Send message. If text contains attachment marker, upload that part as file."""
        self._reply_before = scan_reply_candidates(self._page)
        self._baseline_snap = _page_state_snapshot(self._page, self.name)
        self._last_prompt = text
        page = self._page

        # Check if there's attachment content to upload
        main_text = text
        if ATTACHMENT_MARKER in text:
            parts = text.split(ATTACHMENT_MARKER, 1)
            main_text = parts[0].strip()
            attachment_content = parts[1].strip()
            self._upload_file(attachment_content)
            page.wait_for_timeout(500)

        # Paste main text via ClipboardEvent
        box = page.locator("#prompt-textarea").first
        box.click()
        page.wait_for_timeout(200)
        page.evaluate(
            '''(t) => {
                const dt = new DataTransfer();
                dt.setData("text/plain", t);
                document.activeElement.dispatchEvent(
                    new ClipboardEvent("paste", {bubbles:true, cancelable:true, clipboardData:dt})
                );
            }''',
            main_text
        )
        page.wait_for_timeout(400)
        try:
            send = page.locator('[data-testid="send-button"]').first
            send.click(timeout=3000)
        except Exception:
            box.press("Enter")

    def _wait_for_response(self) -> str:
        page = self._page
        baseline = getattr(self, '_baseline_snap', _page_state_snapshot(page, self.name))
        wait_for_completion(page, self.name, baseline)
        page.wait_for_timeout(800)

        prompt = getattr(self, '_last_prompt', '')
        text = extract_reply_after_anchor(page, self.name, prompt)
        if text:
            return text

        msgs = page.locator('[data-message-author-role="assistant"]').all()
        if msgs:
            return msgs[-1].inner_text().strip()
        blocks = page.locator('.markdown').all()
        if blocks:
            return blocks[-1].inner_text().strip()
        return "[ChatGPT: could not extract response]"
