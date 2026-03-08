"""
Single-client runner — executed as a subprocess by parallel debate engine.
Reads prompt from stdin, writes answer to stdout as JSON.
"""
import sys, json
sys.path.insert(0, '/Users/zhenchen/Symposium')
sys.path.insert(0, str(__import__('pathlib').Path.home() / 'skill-foundry'))

from pathlib import Path
from tools.stealth_browser.browser import StealthBrowser
from symposium.clients.playwright.claude import ClaudeWebClient
from symposium.clients.playwright.chatgpt import ChatGPTClient
from symposium.clients.playwright.gemini import GeminiWebClient

CLIENTS = {"Claude": ClaudeWebClient, "ChatGPT": ChatGPTClient, "Gemini": GeminiWebClient}
STORAGE = str(Path.home() / '.playwright-stealth/storage/session.json')

def main():
    args = json.loads(sys.argv[1])
    name = args["name"]
    prompt = args["prompt"]
    system = args.get("system", None)

    ClientClass = CLIENTS[name]
    with StealthBrowser(session_path=STORAGE) as sb:
        c = ClientClass(sb.new_page())
        c._init_conversation()
        c._initialized = True
        c.ensure_best_config()
        answer = c.ask(prompt, system=system)
    result = {"name": name, "answer": answer}
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
