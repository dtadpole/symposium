"""
Output formatting — terminal display and Markdown file generation.
"""

from datetime import datetime
from pathlib import Path
from .debate import SymposiumResult

# ANSI colors
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
RESET  = "\033[0m"

AI_COLORS = {
    "Claude": "\033[35m",   # magenta
    "Gemini": "\033[34m",   # blue
    "GPT":    "\033[32m",   # green
}


def ai_color(name: str) -> str:
    return AI_COLORS.get(name, CYAN)


def print_result(result: SymposiumResult):
    sep = f"{DIM}{'─' * 60}{RESET}"

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  🏛  SYMPOSIUM{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}\n")

    print(f"{BOLD}❓ Question{RESET}")
    print(f"   {result.question}\n")

    # ── Round 0 ────────────────────────────────────────────────────────────────
    print(sep)
    print(f"{BOLD}⚗️  Round 0 — Initial Answers{RESET}\n")
    for r in result.round0:
        color = ai_color(r.ai_name)
        print(f"{color}{BOLD}【{r.ai_name}】{RESET}")
        for line in r.answer.splitlines():
            print(f"   {line}")
        print()

    # ── Consensus ──────────────────────────────────────────────────────────────
    print(sep)
    print(f"{BOLD}✅ Consensus{RESET}")
    if result.consensus_points:
        for p in result.consensus_points:
            print(f"   {GREEN}•{RESET} {p}")
    else:
        print(f"   {DIM}(no clear consensus found){RESET}")
    print()

    # ── Disagreements ──────────────────────────────────────────────────────────
    print(f"{BOLD}⚡ Disagreements{RESET}")
    if result.disagreement_topics:
        for d in result.disagreement_topics:
            print(f"   {YELLOW}•{RESET} {d}")
    else:
        print(f"   {DIM}(all AIs largely agree){RESET}")
    print()

    # ── Debate ─────────────────────────────────────────────────────────────────
    if result.debate:
        print(sep)
        print(f"{BOLD}⚔️  Debate{RESET}\n")
        for ex in result.debate:
            ca = ai_color(ex.challenger)
            da = ai_color(ex.defender)
            print(f"{ca}{BOLD}{ex.challenger}{RESET} → challenges {da}{BOLD}{ex.defender}{RESET}")
            for line in ex.challenge.splitlines():
                print(f"   {line}")
            print()

    # ── Synthesis ──────────────────────────────────────────────────────────────
    print(sep)
    synth_color = ai_color(result.synthesizer)
    print(f"{BOLD}✨ Final Synthesis{RESET}  {DIM}(by {result.synthesizer}){RESET}\n")
    for line in result.synthesis.splitlines():
        print(f"   {line}")
    print(f"\n{BOLD}{'═' * 60}{RESET}\n")


def to_markdown(result: SymposiumResult) -> str:
    """Generate a Markdown document from a SymposiumResult."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 🏛 Symposium — {result.question[:60]}",
        f"",
        f"**Date:** {ts}  ",
        f"**Participants:** {', '.join(r.ai_name for r in result.round0)}",
        f"",
        f"---",
        f"",
        f"## ❓ Question",
        f"",
        f"> {result.question}",
        f"",
        f"---",
        f"",
        f"## ⚗️ Round 0 — Initial Answers",
        f"",
    ]

    for r in result.round0:
        lines += [f"### {r.ai_name}", f"", r.answer, f""]

    lines += [
        f"---",
        f"",
        f"## ✅ Consensus",
        f"",
    ]
    if result.consensus_points:
        for p in result.consensus_points:
            lines.append(f"- {p}")
    else:
        lines.append("_No clear consensus found._")
    lines.append("")

    lines += [
        f"## ⚡ Disagreements",
        f"",
    ]
    if result.disagreement_topics:
        for d in result.disagreement_topics:
            lines.append(f"- {d}")
    else:
        lines.append("_All AIs largely agree._")
    lines.append("")

    if result.debate:
        lines += [
            f"---",
            f"",
            f"## ⚔️ Debate",
            f"",
        ]
        for ex in result.debate:
            lines += [
                f"### {ex.challenger} → {ex.defender}",
                f"",
                ex.challenge,
                f"",
            ]

    lines += [
        f"---",
        f"",
        f"## ✨ Final Synthesis",
        f"",
        f"_Synthesized by {result.synthesizer}_",
        f"",
        result.synthesis,
        f"",
    ]

    return "\n".join(lines)


def save_markdown(result: SymposiumResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y-%m-%d")
    safe_q    = result.question[:40].replace("/", "-").replace(":", "").strip()
    filename  = f"{date_str} {safe_q}.md"
    path      = output_dir / filename
    path.write_text(to_markdown(result), encoding="utf-8")
    return path
