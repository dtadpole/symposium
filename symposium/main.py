#!/usr/bin/env python3
"""
Symposium CLI — ask once, let the AIs debate.

Participants (default):
  - Claude  → Playwright web (preserves conversation context)
  - ChatGPT → Playwright web (preserves conversation context)

Gemini removed: quota too low, reasoning depth insufficient.
Analysis and synthesis use Anthropic API (fast, no browser needed).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "skill-foundry"))

from tools.stealth_browser.browser import StealthBrowser
from .clients.playwright.claude import ClaudeWebClient
from .clients.playwright.chatgpt import ChatGPTClient
from .debate import SymposiumEngine
from .output import print_result, save_markdown

STORAGE_FILE = str(Path.home() / ".playwright-stealth/storage/session.json")
DEFAULT_OUTPUT = Path.home() / "Documents/Synced Vault #1/AI Chats/Symposium"


def run(question: str, save: bool = True, debate_rounds: int = 3,
        user_input_fn=None):
    print("\n🏛  Symposium is convening...\n")

    with StealthBrowser(session_path=STORAGE_FILE) as sb:
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

        print(f"\n{len(clients)} participants ready. Starting debate ({debate_rounds} rounds)...\n")

        engine = SymposiumEngine(
            clients=clients,
            debate_rounds=debate_rounds,
            user_input_fn=user_input_fn,
        )
        result = engine.run(question)

    print_result(result)

    if save:
        path = save_markdown(result, DEFAULT_OUTPUT)
        print(f"💾 Saved → {path}\n")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="🏛 Symposium — Multi-AI debate engine (Claude + ChatGPT)"
    )
    parser.add_argument("question", nargs="?", help="Question to debate")
    parser.add_argument("--no-save", action="store_true", help="Don't save to Obsidian")
    parser.add_argument("--rounds", type=int, default=3, help="Debate rounds (default: 3)")
    args = parser.parse_args()

    question = args.question
    if not question:
        print("🏛  Symposium — Multi-AI Debate Engine (Claude + ChatGPT)")
        print("Enter your question (or Ctrl+C to exit):\n")
        try:
            question = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            sys.exit(0)

    if not question:
        print("No question provided.")
        sys.exit(1)

    def user_input_fn(prompt):
        print(prompt[-800:])
        try:
            return input("> ").strip()
        except EOFError:
            return ""

    run(question, save=not args.no_save, debate_rounds=args.rounds,
        user_input_fn=user_input_fn)


if __name__ == "__main__":
    main()
