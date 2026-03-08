#!/usr/bin/env python3
"""
Symposium CLI — ask once, let the AIs debate via browser.

Usage:
  python -m symposium.main "your question"
  python -m symposium.main          # interactive mode
  python -m symposium.main --rounds 2 "..."
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "skill-foundry"))

from tools.stealth_browser.browser import StealthBrowser
from .clients.playwright import ChatGPTClient, ClaudeWebClient, GeminiWebClient
from .debate import SymposiumEngine
from .output import print_result, save_markdown

STORAGE_FILE = str(Path.home() / ".playwright-stealth/storage/session.json")
DEFAULT_OUTPUT = Path.home() / "Documents/Synced Vault #1/AI Chats/Symposium"


def run(question: str, save: bool = True, debate_rounds: int = 1):
    print("\n🏛  Symposium is convening...\n")
    print("Opening browser (headless=False, stealth mode)...")

    with StealthBrowser(session_path=STORAGE_FILE) as sb:
        # Each AI gets its own page (tab) — conversations stay independent
        print("  📄 Opening page for Claude...")
        page_claude = sb.new_page()

        print("  📄 Opening page for ChatGPT...")
        page_gpt = sb.new_page()

        print("  📄 Opening page for Gemini...")
        page_gemini = sb.new_page()

        clients = [
            ClaudeWebClient(page_claude),
            ChatGPTClient(page_gpt),
            GeminiWebClient(page_gemini),
        ]

        print(f"\n✓ 3 AI participants ready: {', '.join(c.name for c in clients)}\n")

        engine = SymposiumEngine(clients, debate_rounds=debate_rounds)
        result = engine.run(
            question,
            verbose_callback=lambda m: print(f"\n{m}")
        )

    print_result(result)

    if save:
        path = save_markdown(result, DEFAULT_OUTPUT)
        print(f"💾 Saved → {path}\n")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="🏛 Symposium — Multi-AI debate engine (browser-based)"
    )
    parser.add_argument("question", nargs="?", help="Question to debate")
    parser.add_argument("--no-save", action="store_true", help="Don't save to Obsidian")
    parser.add_argument("--rounds", type=int, default=1, help="Debate rounds (default: 1)")
    args = parser.parse_args()

    question = args.question
    if not question:
        print("🏛  Symposium — Multi-AI Debate Engine (browser mode)")
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
