#!/usr/bin/env python3
"""
Symposium CLI — ask once, let the AIs debate.

Participants:
  - Claude  → Playwright web (preserves conversation context)
  - ChatGPT → Playwright web (preserves conversation context)

Analysis, synthesis, and judge evaluation use Anthropic API.
Browser windows stay open after debate for review.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "skill-foundry"))

from tools.stealth_browser.browser import StealthBrowser
from .clients.playwright.claude import ClaudeWebClient
from .clients.playwright.chatgpt import ChatGPTClient
from .debate import (
    SymposiumEngine,
    DEFAULT_QUESTION,
    DEFAULT_ROUNDS,
    OPENING_CONTEXT,
)
from .output import print_result, save_markdown

STORAGE_FILE = str(Path.home() / ".playwright-stealth/storage/session.json")
DEFAULT_OUTPUT = Path.home() / "Documents/Synced Vault #1/AI Chats/Symposium"


def run(question: str = "", opening_context: str = "",
        save: bool = True, debate_rounds: int = 0,
        user_input_fn=None):

    question = question or DEFAULT_QUESTION
    opening_context = opening_context or OPENING_CONTEXT
    debate_rounds = debate_rounds or DEFAULT_ROUNDS

    print("\n🏛  Symposium is convening...\n")
    print(f"议题: {question[:80]}...")
    print(f"轮次: {debate_rounds}\n")

    # Start browser — do NOT use context manager (browser stays open after debate)
    sb = StealthBrowser(session_path=STORAGE_FILE, headless=False)
    sb.start()

    try:
        clients = [
            ClaudeWebClient(sb.new_page()),
            ChatGPTClient(sb.new_page()),
        ]

        for c in clients:
            print(f"  ↗ 初始化 {c.name}...")
            c._init_conversation()
            c._initialized = True
            c.ensure_best_config()
            print(f"  ✓ {c.name} ready")

        print(f"\n{len(clients)} 位辩手就绪，开始 {debate_rounds} 轮辩论...\n")

        engine = SymposiumEngine(
            clients=clients,
            debate_rounds=debate_rounds,
            user_input_fn=user_input_fn,
        )
        result = engine.run(question, opening_context=opening_context)

    except Exception as e:
        print(f"\n❌ 辩论异常终止: {e}")
        raise
    # NOTE: sb.stop() deliberately NOT called — browser stays open for review

    print_result(result)

    if save:
        path = save_markdown(result, DEFAULT_OUTPUT)
        print(f"💾 Saved → {path}\n")

    return result


def main():
    def user_input_fn(prompt):
        print(prompt[-800:])
        try:
            return input("> ").strip()
        except EOFError:
            return ""

    run(user_input_fn=user_input_fn)


if __name__ == "__main__":
    main()
