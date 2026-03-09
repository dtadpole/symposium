"""Claude web client via Playwright — with Cloudflare/Turnstile handling."""

import time
import random
import tempfile
import os
from .base import PlaywrightChatClient
from .chooser import choose_best
from .reply_extractor import scan_reply_candidates, extract_reply
from .response_waiter import _page_state_snapshot, wait_for_completion, extract_reply_after_anchor


class ClaudeWebClient(PlaywrightChatClient):
    name = "Claude"
    start_url = "https://claude.ai/new"

    def ensure_best_config(self):
        page = self._page
        try:
            btn = page.locator('[data-testid="model-selector-dropdown"]').first
            current = btn.inner_text(timeout=2000).strip()
            btn.click(timeout=3000)
            page.wait_for_timeout(1200)
            options = page.evaluate('''() => [...document.querySelectorAll('[role="menuitem"],button,[role="option"]')]
                .map(el => (el.innerText||el.textContent||'').trim())
                .filter(Boolean)
                .filter(t => t.length < 120)
                .slice(0,120)''')
            choice = choose_best('Claude', current, options)
            for target in [choice.get('target_model', ''), choice.get('target_mode', '')]:
                if not target:
                    continue
                try:
                    el = page.locator(f'button:has-text("{target}")').first
                    if el.is_visible(timeout=1000):
                        el.click(timeout=2000)
                        page.wait_for_timeout(700)
                except Exception:
                    pass
            try:
                page.keyboard.press('Escape')
                page.wait_for_timeout(300)
            except Exception:
                pass
        except Exception:
            pass

    def _human_move(self):
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
        page = self._page
        for _ in range(20):
            url = page.url
            title = page.title()
            if 'challenge' in url or 'just a moment' in title.lower() or 'security' in title.lower():
                self._human_move()
                page.wait_for_timeout(1000)
            else:
                return True
        return False

    def _handle_login(self):
        page = self._page
        if '/login' not in page.url:
            return True
        try:
            btn = page.locator('[data-testid="login-with-google"]').first
            btn.wait_for(state='visible', timeout=8000)
            page.wait_for_timeout(random.randint(500, 1200))
            self._human_move()
            btn.click()
            page.wait_for_url('**/claude.ai/**', timeout=20000)
            page.wait_for_timeout(2000)
            return '/login' not in page.url
        except Exception:
            return False

    def _init_conversation(self):
        page = self._page
        page.goto("https://claude.ai", wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(2000, 3500))
        self._handle_cloudflare()
        if '/login' in page.url:
            self._handle_login()
            page.wait_for_timeout(2000)
        page.goto(self.start_url, wait_until="domcontentloaded")
        page.wait_for_timeout(random.randint(2000, 3000))
        self._handle_cloudflare()
        page.wait_for_selector('div[contenteditable="true"], [data-testid="chat-input"], .ProseMirror', timeout=20000)

    def _type_and_send(self, text: str):
        page = self._page
        self._reply_before = scan_reply_candidates(page)
        self._baseline_snap = _page_state_snapshot(page, self.name)
        self._last_prompt = text
        for sel in ['.ProseMirror', 'div[contenteditable="true"]', '[data-testid="chat-input"]']:
            try:
                box = page.locator(sel).first
                if box.is_visible(timeout=2000):
                    box.click()
                    page.wait_for_timeout(random.randint(200, 400))
                    # Use clipboard paste to avoid keyboard.type garbling long text
                    page.evaluate(
                        '''(t) => {
                            const dt = new DataTransfer();
                            dt.setData("text/plain", t);
                            document.activeElement.dispatchEvent(
                                new ClipboardEvent("paste", {bubbles:true, cancelable:true, clipboardData:dt})
                            );
                        }''',
                        text
                    )
                    page.wait_for_timeout(random.randint(300, 500))
                    break
            except Exception:
                continue
        try:
            send = page.locator('button[aria-label="Send message"]').last
            send.click(timeout=3000)
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

    def _upload_file(self, content: str, filename: str = "opponent_argument.txt") -> bool:
        """Upload content as a file attachment to Claude. Returns True if successful."""
        page = self._page
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', prefix='claude_upload_',
                delete=False, encoding='utf-8'
            )
            tmp.write(content)
            tmp.flush()
            tmp_path = tmp.name
            tmp.close()

            # Claude uses a hidden file input — set_input_files directly
            file_input = page.locator('input[type="file"]').first
            if file_input.count() > 0:
                file_input.set_input_files(tmp_path)
                page.wait_for_timeout(1500)
            else:
                # Fallback: look for attach / paperclip button
                for sel in [
                    'button[aria-label*="ttach"]',
                    'button[aria-label*="ile"]',
                    '[data-testid*="attach"]',
                ]:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=1000):
                            with page.expect_file_chooser() as fc_info:
                                btn.click()
                            fc = fc_info.value
                            fc.set_files(tmp_path)
                            page.wait_for_timeout(1500)
                            break
                    except Exception:
                        continue

            # Rename the tmp file to desired filename if possible
            os.unlink(tmp_path)

            # Verify attachment appeared
            for sel in ['[class*="file"]', '[data-testid*="attachment"]', '[aria-label*="attachment"]']:
                try:
                    if page.locator(sel).count() > 0:
                        return True
                except Exception:
                    pass
            return True  # optimistic if no error thrown
        except Exception:
            return False

    def _wait_for_response(self) -> str:
        page = self._page
        baseline = getattr(self, '_baseline_snap', _page_state_snapshot(page, self.name))
        status = wait_for_completion(page, self.name, baseline)
        page.wait_for_timeout(800)

        prompt = getattr(self, '_last_prompt', '')
        text = extract_reply_after_anchor(page, self.name, prompt)
        if text:
            return text

        # Fallback: parse body text and strip UI boilerplate.
        try:
            body = page.evaluate('() => document.body.innerText') or ''
            lines = [x.strip() for x in body.splitlines() if x.strip()]
            blacklist_exact = {
                'New chat','Search','Customize','Chats','Projects','Artifacts','Code','Recents','Hide',
                'All chats','Share','Write','Learn','Life stuff','From Drive','More models',
                'Claude is AI and can make mistakes. Please double-check responses.',
            }
            def is_time_line(s: str) -> bool:
                return s.endswith('AM') or s.endswith('PM')
            def is_model_line(s: str) -> bool:
                return s.startswith(('Sonnet', 'Opus', 'Haiku')) or s in {'Extended', 'Extended thinking'}
            cleaned = []
            for ln in lines:
                if ln in blacklist_exact:
                    continue
                if is_time_line(ln):
                    continue
                if is_model_line(ln):
                    continue
                cleaned.append(ln)
            for ln in reversed(cleaned):
                if ln in {'Z', 'Zhen', 'Max plan'}:
                    continue
                if len(ln) > 0:
                    return ln
        except Exception:
            pass

        return "[Claude: could not extract response]"
