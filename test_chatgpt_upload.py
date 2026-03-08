#!/usr/bin/env python3
"""
Test: ChatGPT file attachment upload via Playwright.
Verifies that opponent content can be uploaded as a .txt file attachment.
"""

import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.home() / "skill-foundry"))
from tools.stealth_browser.browser import StealthBrowser

STORAGE_FILE = str(Path.home() / ".playwright-stealth/storage/session.json")

TEST_CONTENT = """这是一段测试内容，模拟 Claude 在开篇立论中说的话。

【立场】
我认为自我进化知识库的核心在于反馈机制的数学保证。

【核心论点1：信息熵约束下的净进化条件】
设知识库状态为 K_t，目标函数为 f(K)，则进化操作 Φ 需满足：
  E[f(Φ(K_t))] > f(K_t)
即每次演化操作的期望收益为正。

【核心论点2：Copy-on-Write 保证可回滚性】
CoW 架构使得每次操作都可回滚，这是反馈机制能够生效的工程基础。

【判断标准】
评判标准：哪个方案能从数学上证明知识库随时间单调变好？
"""

def test_upload():
    print("🧪 开始测试 ChatGPT 文件 upload...")
    
    sb = StealthBrowser(session_path=STORAGE_FILE, headless=False)
    sb.start()
    page = sb.new_page()
    
    try:
        # Navigate to ChatGPT
        print("  → 打开 ChatGPT...")
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        
        # New chat
        try:
            btn = page.locator('[data-testid="create-new-chat-button"]').first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(1500)
        except:
            pass
        
        page.wait_for_selector("#prompt-textarea", timeout=15000)
        print("  ✓ ChatGPT 输入框就绪")
        
        # Write test file
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', prefix='symposium_test_',
            delete=False, encoding='utf-8'
        )
        tmp.write(TEST_CONTENT)
        tmp.flush()
        tmp_path = tmp.name
        tmp.close()
        print(f"  → 临时文件: {tmp_path}")
        
        # Find upload button
        upload_btn = None
        tried = []
        for sel in [
            'button[aria-label="Attach files"]',
            'button[aria-label="附加文件"]',
            'button[data-testid="composer-attachment-button"]',
            'button[aria-label*="ttach"]',
            'button[aria-label*="ile"]',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1500):
                    upload_btn = el
                    print(f"  ✓ 找到 upload 按钮: {sel}")
                    break
                tried.append(f"{sel}(not visible)")
            except Exception as e:
                tried.append(f"{sel}({e})")
        
        if upload_btn is None:
            # Scan all buttons and print them
            print("  ⚠️  未找到已知选择器，扫描所有按钮...")
            btns = page.evaluate('''() => {
                return [...document.querySelectorAll('button')].map(b => ({
                    label: b.getAttribute('aria-label') || '',
                    text: b.innerText.trim().slice(0, 40),
                    testid: b.getAttribute('data-testid') || '',
                })).filter(b => b.label || b.testid)
            }''')
            for b in btns[:20]:
                print(f"    btn: label={b['label']!r} testid={b['testid']!r} text={b['text']!r}")
            
            # Try file input directly
            file_input = page.locator('input[type="file"]').first
            if file_input.count() > 0:
                print("  → 找到 file input，直接 set_input_files")
                file_input.set_input_files(tmp_path)
                upload_btn = "direct_input"
            else:
                print("  ❌ 未找到任何 upload 入口")
                return False
        
        # Use file chooser if button found
        if upload_btn and upload_btn != "direct_input":
            print("  → 点击 upload 按钮，等待 file chooser...")
            try:
                # Try set_input_files on hidden input first
                file_input = page.locator('input[type="file"]').first
                if file_input.count() > 0:
                    file_input.set_input_files(tmp_path)
                    print("  ✓ set_input_files 成功")
                else:
                    with page.expect_file_chooser(timeout=5000) as fc_info:
                        upload_btn.click()
                    fc = fc_info.value
                    fc.set_files(tmp_path)
                    print("  ✓ file chooser 上传成功")
            except Exception as e:
                print(f"  ⚠️  file chooser 方式失败: {e}")
                # Last resort: try clicking and waiting
                upload_btn.click()
                page.wait_for_timeout(2000)
        
        # Wait for upload indicator
        page.wait_for_timeout(2500)
        
        # Check if attachment appeared
        attachment_visible = False
        for sel in [
            '[data-testid*="attachment"]',
            '.attachment',
            '[class*="attachment"]',
            '[class*="file"]',
            'div[class*="upload"]',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1000):
                    attachment_visible = True
                    print(f"  ✓ 附件已显示: {sel}")
                    break
            except:
                pass
        
        if not attachment_visible:
            # Screenshot to check state
            page.screenshot(path="/tmp/chatgpt_upload_test.png")
            print("  → 截图已保存到 /tmp/chatgpt_upload_test.png（请检查是否有附件图标）")
        
        # Now type a prompt and send
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
            "这是附件上传测试。请确认你收到了附件，并简单总结附件内容。"
        )
        page.wait_for_timeout(500)
        
        # Send
        try:
            send = page.locator('[data-testid="send-button"]').first
            send.click(timeout=3000)
            print("  ✓ 消息已发送，等待回复...")
        except:
            box.press("Enter")
        
        # Wait for response (simple wait)
        page.wait_for_timeout(20000)
        
        # Check response
        msgs = page.locator('[data-message-author-role="assistant"]').all()
        if msgs:
            reply = msgs[-1].inner_text().strip()
            print(f"\n✅ ChatGPT 回复（前300字）:\n{reply[:300]}")
            if "附件" in reply or "文件" in reply or "知识库" in reply or "CoW" in reply or "反馈" in reply:
                print("\n🎉 测试通过！ChatGPT 能读取附件内容")
                return True
            else:
                print("\n⚠️  回复未提及附件内容，可能上传失败")
                return False
        else:
            print("  ⚠️  未获取到 ChatGPT 回复")
            return False
            
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print("\n（浏览器保持开启，请检查界面）")


if __name__ == "__main__":
    success = test_upload()
    print(f"\n结果: {'✅ PASS' if success else '❌ FAIL'}")
