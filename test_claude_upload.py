"""
Quick test: upload a .txt file to Claude and verify it reads the content.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / 'skill-foundry'))
sys.path.insert(0, str(Path(__file__).parent))

from tools.stealth_browser.browser import StealthBrowser
from symposium.clients.playwright.claude import ClaudeWebClient

TEST_CONTENT = """# 对手立论（测试文件）

## 核心主张
这是一个测试文件，用于验证 Claude 是否能读取上传的附件内容。

## 关键论点
1. 知识单元应当通过可证伪假说驱动进化
2. 每条 Knowledge Unit 必须内嵌验证条件
3. Ablation Study 成本应通过 Applicability Scope 自适应控制

## 结论
如果 Claude 能看到这些内容，说明文件上传机制工作正常。
请在回复中引用"Applicability Scope"这个词，以确认你读到了附件。
"""

PROMPT = """请阅读我上传的附件文件 opponent_argument.txt，
然后简短说明：你看到了附件里的哪些内容？（用一两句话即可，不需要长篇大论）"""

def main():
    sb = StealthBrowser(
        headless=False,
        session_path="~/.playwright-stealth/storage/session.json"
    )
    sb.start()
    page = sb.new_page()

    client = ClaudeWebClient(page)
    print("初始化 Claude...")
    client._init_conversation()
    client.ensure_best_config()
    print("✓ Claude ready")

    # Upload file
    print(f"\n上传附件 (opponent_argument.txt, {len(TEST_CONTENT)} 字符)...")
    ok = client._upload_file(TEST_CONTENT, filename="opponent_argument.txt")
    print(f"上传结果: {'✅ 成功' if ok else '❌ 失败'}")

    if ok:
        print("\n发送提问...")
        client._type_and_send(PROMPT)
        print("等待回复...")
        time.sleep(5)
        reply = client._wait_for_response()
        print(f"\n=== Claude 回复 ===\n{reply}\n===================")
        if "Applicability Scope" in reply or "附件" in reply or "文件" in reply:
            print("✅ Claude 成功读取了附件内容！")
        else:
            print("⚠️  回复中未找到预期关键词，请手动确认浏览器窗口")
    else:
        print("❌ 上传失败，请检查 Claude 页面是否支持文件上传")

    print("\n测试完成，浏览器保持开启供检查...")
    # Don't close browser

if __name__ == "__main__":
    main()
