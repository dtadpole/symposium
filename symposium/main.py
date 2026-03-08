#!/usr/bin/env python3
"""
Symposium CLI — ask once, let the AIs debate.

Backend strategy (most reliable first):
  - Claude  → Anthropic API  (always stable, no browser needed)
  - ChatGPT → Playwright web (reuses existing browser session)
  - Gemini  → Playwright web (reuses existing browser session)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "skill-foundry"))

from tools.stealth_browser.browser import StealthBrowser
from .clients.claude import ClaudeClient
from .clients.playwright import ChatGPTClient, GeminiWebClient
from .debate import SymposiumEngine
from .output import print_result, save_markdown

STORAGE_FILE = str(Path.home() / ".playwright-stealth/storage/session.json")
DEFAULT_OUTPUT = Path.home() / "Documents/Synced Vault #1/AI Chats/Symposium"


def run(question: str, save: bool = True, debate_rounds: int = 1):
    print("\n🏛  Symposium is convening...\n")

    # Claude via API
    print("  ✓ Claude  (API)")
    claude = ClaudeClient()

    # ChatGPT + Gemini via browser
    print("  ↗ Opening browser for ChatGPT & Gemini...")
    sb = StealthBrowser(session_path=STORAGE_FILE)
    sb.start()

    page_gpt    = sb.new_page()
    page_gemini = sb.new_page()

    clients = [
        claude,
        ChatGPTClient(page_gpt),
        GeminiWebClient(page_gemini),
    ]

    print(f"  ✓ ChatGPT (browser)")
    print(f"  ✓ Gemini  (browser)")
    print(f"\n3 participants ready. Starting debate...\n")

    try:
        engine = SymposiumEngine(clients, debate_rounds=debate_rounds)
        result = engine.run(question, verbose_callback=lambda m: print(f"\n{m}"))
    finally:
        sb.stop()

    print_result(result)

    if save:
        path = save_markdown(result, DEFAULT_OUTPUT)
        print(f"💾 Saved → {path}\n")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="🏛 Symposium — Multi-AI debate engine"
    )
    parser.add_argument("question", nargs="?", help="Question to debate")
    parser.add_argument("--no-save", action="store_true", help="Don't save to Obsidian")
    parser.add_argument("--rounds", type=int, default=1, help="Debate rounds (default: 1)")
    args = parser.parse_args()

    question = args.question
    if not question:
        print("🏛  Symposium — Multi-AI Debate Engine")
        print("Enter your question (or Ctrl+C to exit):\n")
        try:
            question = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            sys.exit(0)

    if not question:
        print("No question provided.")
        sys.exit(1)

    run(question, save=not args.no_save, debate_rounds=args.rounds)


if __name__ == "__main__":
    main()
