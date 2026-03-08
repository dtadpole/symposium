#!/usr/bin/env python3
"""
Symposium CLI — ask once, let the AIs debate.

Usage:
  python -m symposium.main "your question here"
  python -m symposium.main  # interactive mode
"""

import argparse
import sys
from pathlib import Path

from .config import available_clients
from .clients.claude import ClaudeClient
from .clients.gemini import GeminiClient
from .clients.gpt import GPTClient
from .debate import SymposiumEngine
from .output import print_result, save_markdown

# Default Obsidian output folder
DEFAULT_OUTPUT = Path.home() / "Documents/Synced Vault #1/AI Chats/Symposium"


def build_clients():
    clients = []
    available = available_clients()

    if "claude" in available:
        try:
            clients.append(ClaudeClient())
            print("  ✓ Claude")
        except Exception as e:
            print(f"  ✗ Claude: {e}")

    if "gemini" in available:
        try:
            clients.append(GeminiClient())
            print("  ✓ Gemini")
        except Exception as e:
            print(f"  ✗ Gemini: {e}")

    if "gpt" in available:
        try:
            clients.append(GPTClient())
            print("  ✓ GPT")
        except Exception as e:
            print(f"  ✗ GPT: {e}")

    return clients


def run(question: str, save: bool = True, debate_rounds: int = 1):
    print("\n🏛  Symposium is convening...\n")
    print("Participants:")
    clients = build_clients()

    if len(clients) < 2:
        print("\n❌ Need at least 2 configured AI clients. Check your API keys.")
        sys.exit(1)

    engine = SymposiumEngine(clients, debate_rounds=debate_rounds)
    result = engine.run(question, verbose_callback=lambda m: print(f"\n{m}"))

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
