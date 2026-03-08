"""
Core debate engine for Symposium — with user-as-judge participation.

Flow:
  1. Round 0  — All AIs answer independently
  2. Compare  — Show user the consensus/disagreements, ask for guidance
  3. Debate   — With user guidance integrated into prompts; ring-rotation challenges
  4. Compare  — Show debate results, ask user for further direction
  5. Synthesis — Final answer incorporating everything
"""

import concurrent.futures
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from .clients.base import AIClient

# ── Prompts ────────────────────────────────────────────────────────────────────

COMPARE_SYSTEM = """You are a neutral debate moderator summarizing multiple AI responses.
Be concise and specific. Output in this exact format:

CONSENSUS:
- <point 1>
...

DISAGREEMENTS:
- <topic>: <positions summary>
...

INTERESTING_GAPS:
- <what nobody addressed but should>
...
"""

CHALLENGE_SYSTEM = """You are participating in a structured intellectual debate.
You will be shown another AI's answer plus the user's guidance as debate judge.
Your job:
1. Identify the strongest points in their reasoning
2. Identify any flaws, gaps, or oversimplifications
3. Incorporate the user's guidance and questions
4. Provide your own refined and concrete position

Be direct, intellectually rigorous, and specific. No vague frameworks."""

SYNTHESIS_SYSTEM = """You are the final synthesizer in a multi-AI debate.
You have the original question, all answers, user guidance, and the full debate.
Produce the single best answer possible — concrete, specific, engineering-grade.
Resolve disagreements with clear reasoning. This is the definitive answer."""


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class Round0Response:
    ai_name: str
    answer: str

@dataclass
class DebateExchange:
    challenger: str
    defender: str
    round_num: int
    user_guidance: str
    challenge: str

@dataclass
class SymposiumResult:
    question: str
    round0: list[Round0Response]
    consensus_points: list[str]
    disagreement_topics: list[str]
    gaps: list[str]
    user_guidance_r0: str       # user input after round 0
    debate: list[DebateExchange]
    user_guidance_post_debate: str
    synthesis: str
    synthesizer: str


# ── Engine ─────────────────────────────────────────────────────────────────────

class SymposiumEngine:
    def __init__(
        self,
        client_names: list[str],
        analysis_client: AIClient,
        debate_rounds: int = 1,
        user_input_fn: Callable[[str], str] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ):
        """
        client_names    : list of AI names to use via subprocess ["Claude","ChatGPT","Gemini"]
        analysis_client : AIClient used sequentially for analysis + synthesis
        debate_rounds   : how many challenge rounds
        user_input_fn   : fn(prompt_for_user) -> user's text; if None, skip user turns
        log_fn          : fn(msg) for progress logging
        """
        if len(client_names) < 2:
            raise ValueError("Need at least 2 AI clients")
        self.client_names = client_names
        self.clients = [analysis_client]   # kept for backward compat (analysis/synthesis)
        self.debate_rounds = debate_rounds
        self.user_input_fn = user_input_fn
        self.log_fn = log_fn

    def _log(self, msg: str):
        if self.log_fn:
            self.log_fn(msg)
        else:
            print(msg)

    def _ask(self, client: AIClient, prompt: str, system: str | None = None) -> str:
        """Sequential ask (used for analysis/synthesis with first client)."""
        try:
            return client.ask(prompt, system=system)
        except Exception as e:
            return f"[Error from {client.name}: {e}]"

    def _run_subprocess(self, name: str, prompt: str, system: str | None) -> Round0Response:
        """Run one client in a separate subprocess to avoid Playwright thread issues."""
        runner = str(Path(__file__).parent / "runner.py")
        arg = json.dumps({"name": name, "prompt": prompt, "system": system})
        self._log(f"   → {name} 回答中（subprocess）...")
        try:
            r = subprocess.run(
                [sys.executable, runner, arg],
                capture_output=True, text=True, timeout=1200
            )
            if r.returncode == 0:
                data = json.loads(r.stdout.strip().splitlines()[-1])
                ans = data.get("answer", "[no answer]")
            else:
                ans = f"[{name} error: {r.stderr[-300:]}]"
        except Exception as e:
            ans = f"[{name} subprocess error: {e}]"
        self._log(f"   ✓ {name} 完成 ({len(ans)} chars)")
        return Round0Response(name, ans)

    def _ask_parallel(self, tasks: list[tuple[str, str, str | None]]) -> list[Round0Response]:
        """Run multiple clients in parallel subprocesses."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as ex:
            futs = [ex.submit(self._run_subprocess, name, prompt, system)
                    for name, prompt, system in tasks]
            return [f.result() for f in concurrent.futures.as_completed(futs)]

    def _ask_user(self, prompt: str) -> str:
        if self.user_input_fn:
            return self.user_input_fn(prompt)
        return ""

    def _compare(self, question: str, answers: list[tuple[str, str]]) -> tuple[list, list, list, str]:
        """Use first client to produce consensus/disagreement/gap analysis."""
        combined = "\n\n".join(f"=== {name} ===\n{ans}" for name, ans in answers)
        analysis_prompt = (
            f"Original question: {question}\n\n"
            f"Answers from {len(answers)} AIs:\n\n{combined}"
        )
        raw = self._ask(self.clients[0], analysis_prompt, system=COMPARE_SYSTEM)
        consensus, disagreements, gaps = [], [], []
        section = None
        for line in raw.splitlines():
            l = line.strip()
            if l.upper().startswith("CONSENSUS"):
                section = "c"
            elif l.upper().startswith("DISAGREE"):
                section = "d"
            elif l.upper().startswith("INTERESTING_GAP") or l.upper().startswith("GAP"):
                section = "g"
            elif l.startswith("-"):
                item = l[1:].strip()
                if section == "c":
                    consensus.append(item)
                elif section == "d":
                    disagreements.append(item)
                elif section == "g":
                    gaps.append(item)
        return consensus, disagreements, gaps, raw

    def _format_comparison(self, question: str, round0: list[Round0Response],
                            consensus: list, disagreements: list, gaps: list) -> str:
        lines = [
            "=" * 60,
            f"📋 ROUND 0 RESULTS — 三家 AI 的回答比较",
            "=" * 60,
            "",
        ]
        for r in round0:
            lines += [f"── {r.ai_name} ──", r.answer[:600] + ("..." if len(r.answer) > 600 else ""), ""]

        lines += ["─" * 40, "✅ 共识点："]
        for p in consensus:
            lines.append(f"  • {p}")
        lines += ["", "⚡ 分歧点："]
        for d in disagreements:
            lines.append(f"  • {d}")
        if gaps:
            lines += ["", "❓ 没人提到但值得深挖："]
            for g in gaps:
                lines.append(f"  • {g}")
        lines += ["", "=" * 60]
        return "\n".join(lines)

    def run(self, question: str) -> SymposiumResult:

        # ── Round 0: parallel subprocesses ────────────────────────────────────
        self._log("⚗️  Round 0: 三家 AI 并行回答（subprocess）...")
        round0 = self._ask_parallel(
            [(name, question, None) for name in self.client_names]
        )
        round0 = sorted(round0, key=lambda r: self.client_names.index(r.ai_name))

        # ── Compare after Round 0 ─────────────────────────────────────────────
        self._log("🔍 分析共识与分歧（Claude API）...")
        answers_r0 = [(r.ai_name, r.answer) for r in round0]
        consensus, disagreements, gaps, _ = self._compare(question, answers_r0)

        comparison_text = self._format_comparison(question, round0, consensus, disagreements, gaps)
        self._log(comparison_text)

        # ── User input after Round 0 ──────────────────────────────────────────
        user_guidance_r0 = self._ask_user(
            comparison_text + "\n\n"
            "作为裁判，请输入你的质询或引导方向（直接回车跳过）:\n> "
        )
        if user_guidance_r0:
            self._log(f"👤 用户引导: {user_guidance_r0}")

        # ── Debate rounds ─────────────────────────────────────────────────────
        self._log(f"⚔️  辩论开始（{self.debate_rounds} 轮）...")
        debate_exchanges: list[DebateExchange] = []
        current_answers: dict[str, str] = {r.ai_name: r.answer for r in round0}

        for rnd in range(self.debate_rounds):
            self._log(f"   第 {rnd+1} 轮（并行）...")
            tasks = []  # (challenger_name, defender_name, prompt)
            for i, name in enumerate(self.client_names):
                defender_name = self.client_names[(i + 1) % len(self.client_names)]
                defender_ans = current_answers.get(defender_name, "")
                challenge_prompt = (
                    f"原始问题: {question}\n\n"
                    f"{defender_name} 的回答:\n{defender_ans}\n\n"
                    f"分歧点:\n" + "\n".join(f"- {d}" for d in disagreements) +
                    ("\n\n用户（裁判）的引导:\n" + user_guidance_r0 if user_guidance_r0 else "") +
                    f"\n\n请从你自己的视角挑战 {defender_name} 的观点，要具体、工程化。"
                )
                tasks.append((name, defender_name, challenge_prompt))

            challenge_inputs = [(name, prompt, CHALLENGE_SYSTEM) for name, _, prompt in tasks]
            challenge_responses = self._ask_parallel(challenge_inputs)
            cr_map = {r.ai_name: r.answer for r in challenge_responses}
            results = [(name, defender_name, cr_map.get(name, "[no response]"))
                       for name, defender_name, _ in tasks]

            for challenger_name, defender_name, challenge in results:
                debate_exchanges.append(DebateExchange(
                    challenger=challenger_name,
                    defender=defender_name,
                    round_num=rnd + 1,
                    user_guidance=user_guidance_r0,
                    challenge=challenge,
                ))
                old = current_answers.get(defender_name, "")
                current_answers[defender_name] = (
                    f"{old}\n\n[{challenger_name} 挑战后]:\n{challenge}"
                )

        # ── Compare after debate ───────────────────────────────────────────────
        post_debate_answers = [(ex.challenger, ex.challenge) for ex in debate_exchanges]
        c2, d2, g2, _ = self._compare(question, post_debate_answers)
        post_debate_summary = self._format_comparison(
            question,
            [Round0Response(ex.challenger, ex.challenge) for ex in debate_exchanges],
            c2, d2, g2)
        self._log("\n📊 辩论后比较:\n" + post_debate_summary)

        user_guidance_post = self._ask_user(
            post_debate_summary + "\n\n"
            "辩论已结束。请输入最终引导（直接回车进入综合）:\n> "
        )
        if user_guidance_post:
            self._log(f"👤 用户最终引导: {user_guidance_post}")

        # ── Synthesis ─────────────────────────────────────────────────────────
        self._log("✨ 综合最终答案...")
        debate_text = "\n\n".join(
            f"--- 第{ex.round_num}轮: {ex.challenger} 挑战 {ex.defender} ---\n{ex.challenge}"
            for ex in debate_exchanges
        )
        combined_r0 = "\n\n".join(f"=== {r.ai_name} ===\n{r.answer}" for r in round0)
        synthesis_prompt = (
            f"原始问题: {question}\n\n"
            f"=== 初始回答 ===\n{combined_r0}\n\n"
            f"=== 共识点 ===\n" + "\n".join(f"- {p}" for p in consensus) +
            f"\n\n=== 辩论记录 ===\n{debate_text}\n\n" +
            (f"=== 用户（裁判）的引导 ===\n{user_guidance_r0}\n{user_guidance_post}\n\n"
             if user_guidance_r0 or user_guidance_post else "") +
            "请给出最终、最具体、工程化的综合答案。"
        )
        # Synthesis via last subprocess client
        synth_name = self.client_names[-1]
        synth_result = self._run_subprocess(synth_name, synthesis_prompt, SYNTHESIS_SYSTEM)
        synthesis = synth_result.answer
        self._log(f"✓ 综合完成 ({len(synthesis)} chars)")

        return SymposiumResult(
            question=question,
            round0=round0,
            consensus_points=consensus,
            disagreement_topics=disagreements,
            gaps=gaps,
            user_guidance_r0=user_guidance_r0,
            debate=debate_exchanges,
            user_guidance_post_debate=user_guidance_post,
            synthesis=synthesis,
            synthesizer=synth_name,
        )
