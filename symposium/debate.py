"""
Core debate engine for Symposium — with user-as-judge participation.

Flow:
  1. Round 0  — All AIs answer independently
  2. Compare  — Show user the consensus/disagreements, ask for guidance
  3. Debate   — With user guidance integrated into prompts; ring-rotation challenges
  4. Compare  — Show debate results, ask user for further direction
  5. Synthesis — Final answer incorporating everything
"""

from dataclasses import dataclass, field
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
        clients: list[AIClient],
        debate_rounds: int = 1,
        user_input_fn: Callable[[str], str] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ):
        """
        clients       : list of AIClient instances (sequential, not concurrent)
        debate_rounds : how many challenge rounds
        user_input_fn : fn(prompt_for_user) -> user's text; if None, skip user turns
        log_fn        : fn(msg) for progress logging
        """
        if len(clients) < 2:
            raise ValueError("Need at least 2 AI clients")
        self.clients = clients
        self.debate_rounds = debate_rounds
        self.user_input_fn = user_input_fn
        self.log_fn = log_fn

    def _log(self, msg: str):
        if self.log_fn:
            self.log_fn(msg)
        else:
            print(msg)

    def _ask(self, client: AIClient, prompt: str, system: str | None = None) -> str:
        try:
            return client.ask(prompt, system=system)
        except Exception as e:
            return f"[Error from {client.name}: {e}]"

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

        # ── Round 0 ───────────────────────────────────────────────────────────
        self._log("⚗️  Round 0: 三家 AI 独立回答中...")
        round0 = []
        for c in self.clients:
            self._log(f"   → {c.name} 回答中...")
            ans = self._ask(c, question)
            round0.append(Round0Response(c.name, ans))
            self._log(f"   ✓ {c.name} 完成 ({len(ans)} chars)")

        # ── Compare after Round 0 ─────────────────────────────────────────────
        self._log("🔍 分析共识与分歧...")
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
        current_answers = {r.ai_name: r.answer for r in round0}

        for rnd in range(self.debate_rounds):
            self._log(f"   第 {rnd+1} 轮...")
            for i, client in enumerate(self.clients):
                defender_client = self.clients[(i + 1) % len(self.clients)]
                defender_ans = current_answers.get(defender_client.name, "")

                challenge_prompt = (
                    f"原始问题: {question}\n\n"
                    f"{defender_client.name} 的回答:\n{defender_ans}\n\n"
                    f"分歧点:\n" + "\n".join(f"- {d}" for d in disagreements) +
                    ("\n\n用户（裁判）的引导:\n" + user_guidance_r0 if user_guidance_r0 else "") +
                    f"\n\n请从你自己的视角挑战 {defender_client.name} 的观点，要具体、工程化。"
                )
                self._log(f"   {client.name} → 挑战 {defender_client.name}...")
                challenge = self._ask(client, challenge_prompt, system=CHALLENGE_SYSTEM)
                self._log(f"   ✓ {client.name} 完成 ({len(challenge)} chars)")

                debate_exchanges.append(DebateExchange(
                    challenger=client.name,
                    defender=defender_client.name,
                    round_num=rnd + 1,
                    user_guidance=user_guidance_r0,
                    challenge=challenge,
                ))
                current_answers[defender_client.name] = (
                    f"{defender_ans}\n\n[{client.name} 挑战后]:\n{challenge}"
                )

        # ── Compare after debate ───────────────────────────────────────────────
        post_debate_answers = [(ex.challenger, ex.challenge) for ex in debate_exchanges]
        c2, d2, g2, _ = self._compare(question, post_debate_answers)
        post_debate_summary = self._format_comparison(question,
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
        synthesizer = self.clients[-1]
        synthesis = self._ask(synthesizer, synthesis_prompt, system=SYNTHESIS_SYSTEM)
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
            synthesizer=synthesizer.name,
        )
