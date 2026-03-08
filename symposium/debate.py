"""
Symposium Debate Engine — context-preserving multi-round debate.

Architecture:
- 3 browser pages opened ONCE at startup and kept alive throughout
- Each round sends follow-up messages in the SAME conversation (full context)
- Analysis / summarization via Claude API (fast, no browser needed)
- Sequential send + sequential wait (Playwright is greenlet-bound)
- User acts as judge between rounds

Flow per round:
  send C1 → wait C1 → send C2 → wait C2 → send C3 → wait C3
  → API summarize → show user → get user guidance → next round
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import anthropic

import time

from .clients.base import AIClient
from .clients.playwright.response_waiter import check_done, extract_reply_after_anchor


def _load_openclaw_anthropic() -> tuple[str | None, str]:
    """Load API key + model from OpenClaw config. Falls back to env / defaults."""
    import os, json as _json
    from pathlib import Path as _Path
    key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    try:
        p = _Path.home() / ".openclaw/agents/main/agent/auth-profiles.json"
        data = _json.loads(p.read_text())
        key = key or data["profiles"]["anthropic:default"]["token"]
        # model from openclaw config if present
        model = data.get("defaultModel", model).replace("anthropic/", "")
    except Exception:
        pass
    return key, model

# ── Prompts ────────────────────────────────────────────────────────────────────

# Prepended to EVERY message sent to any AI
REPLY_FORMAT_RULE = (
    "【重要格式要求】请将你的完整回答直接写在对话主回复中，"
    "不要创建任何文档、artifact、代码块外的独立文件或附件。"
    "所有内容必须在这条消息的正文里。\n\n"
)

CHALLENGE_TMPL = """{other_name} 对你之前的方案提出了以下观点：

{other_answer}

请基于你在这次对话中已有的方案，回应 {other_name} 的观点：
1. 指出 {other_name} 方案中的优点和你认同的地方
2. 指出 {other_name} 方案中的不足或你不同意的地方
3. 结合 {other_name} 的挑战，对你自己的方案进行具体的改进或补充

请保持工程化和具体，不要只给大框架。"""

USER_GUIDANCE_TMPL = """用户（裁判）对本轮讨论有以下引导：

{guidance}

请结合以上引导，对你的方案进行回应和补充。"""

SYNTHESIS_SYSTEM = """You are the final synthesizer in a multi-AI debate.
Given the full debate history, produce the single best answer.
Be concrete, engineering-grade, and definitive."""


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class RoundResult:
    round_num: int
    answers: dict[str, str]   # name -> answer
    user_guidance: str = ""

@dataclass
class SymposiumResult:
    question: str
    rounds: list[RoundResult]
    synthesis: str
    synthesizer: str


# ── Engine ─────────────────────────────────────────────────────────────────────

class SymposiumEngine:
    def __init__(
        self,
        clients: list[AIClient],          # 3 browser clients, kept alive
        api_key: str | None = None,       # Anthropic API key for analysis
        debate_rounds: int = 3,
        user_input_fn: Callable[[str], str] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ):
        if len(clients) < 2:
            raise ValueError("Need at least 2 AI clients")
        self.clients = clients
        self.api_key = api_key
        self.debate_rounds = debate_rounds
        self.user_input_fn = user_input_fn
        self.log_fn = log_fn
        oc_key, oc_model = _load_openclaw_anthropic()
        self._api_key = api_key or oc_key
        self._api_model = oc_model
        self._anthropic = anthropic.Anthropic(api_key=self._api_key) if self._api_key else None

    def _log(self, msg: str):
        if self.log_fn:
            self.log_fn(msg)
        else:
            print(msg, flush=True)

    def _ask(self, client: AIClient, prompt: str, system: str | None = None) -> str:
        try:
            return client.ask(prompt, system=system)
        except Exception as e:
            return f"[Error from {client.name}: {e}]"

    def _ask_all_pipeline(self, clients: list[AIClient], prompt: str) -> dict[str, str]:
        """
        Pipeline: send sequentially (fast), then poll all pages until each is done.
        While waiting for C1, C2 and C3 already have their messages sent.
        """
        POLL_INTERVAL = 15   # seconds between polls
        HARD_TIMEOUT = 900   # 15 min

        full_prompt = REPLY_FORMAT_RULE + prompt

        # Step 1: send to all clients sequentially (fast)
        for c in clients:
            self._log(f"   ✉️  发送给 {c.name}...")
            try:
                c._type_and_send(full_prompt)
            except Exception as e:
                self._log(f"   ⚠️  {c.name} 发送失败: {e}")

        # Step 2: poll all pages until each reports done
        self._log("   ⏳ 等待各方回复（轮询中）...")
        start = time.time()
        pending = {c.name: c for c in clients}
        results: dict[str, str] = {}

        while pending and (time.time() - start) < HARD_TIMEOUT:
            for name in list(pending.keys()):
                c = pending[name]
                try:
                    if check_done(c._page, c.name):
                        ans = extract_reply_after_anchor(c._page, c.name,
                                                         getattr(c, '_last_prompt', prompt))
                        if not ans:
                            ans = c._wait_for_response()
                        results[name] = ans
                        self._log(f"   ✓ {name} 完成 ({len(ans)} chars)")
                        del pending[name]
                except Exception as e:
                    self._log(f"   ⚠️  {name} 轮询出错: {e}")

            if pending:
                self._log(f"   ⏳ 还在等: {list(pending.keys())} "
                          f"(已过 {int(time.time()-start)}s)")
                time.sleep(POLL_INTERVAL)

        # timeout fallback
        for name, c in pending.items():
            self._log(f"   ⏰ {name} 超时，尝试强制提取...")
            try:
                results[name] = c._wait_for_response()
            except Exception as e:
                results[name] = f"[{name} timeout: {e}]"

        return results

    def _ask_user(self, display: str) -> str:
        if self.user_input_fn:
            return self.user_input_fn(display)
        return ""

    def _summarize_for_challenge(self, name: str, answer: str) -> str:
        """Summarize one AI's answer to key points for use in a challenge prompt."""
        if not self._anthropic or len(answer) < 800:
            return answer  # short enough, use as-is
        prompt = (
            f"以下是 {name} 对知识库问题的回答，请提炼出最核心的3-5个论点，"
            f"每点1-2句话，保留具体数据结构或机制名称，去掉重复和废话：\n\n{answer}"
        )
        try:
            msg = self._anthropic.messages.create(
                model=self._api_model,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception:
            return answer[:1500]  # fallback: truncate

    def _api_summarize(self, question: str, answers: dict[str, str]) -> str:
        """Use Claude API to summarize/compare answers. Fast, no browser."""
        if not self._anthropic:
            return ""
        combined = "\n\n".join(f"=== {name} ===\n{ans}" for name, ans in answers.items())
        prompt = (
            f"原始问题: {question}\n\n"
            f"各方回答:\n{combined}\n\n"
            "请分析：\n"
            "1. 各方的共识点\n"
            "2. 各方的主要分歧\n"
            "3. 各方都没有充分讨论但值得深挖的盲点\n\n"
            "请保持简洁，每点不超过2-3句话。"
        )
        try:
            msg = self._anthropic.messages.create(
                model=self._api_model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            return f"[API分析失败: {e}]"

    def _api_synthesis(self, question: str, all_rounds: list[RoundResult]) -> str:
        """Final synthesis via API."""
        if not self._anthropic:
            return "[No API key for synthesis]"
        history = "\n\n".join(
            f"=== 第{r.round_num}轮 ===\n" +
            "\n".join(f"-- {name} --\n{ans}" for name, ans in r.answers.items())
            for r in all_rounds
        )
        prompt = (
            f"原始问题: {question}\n\n"
            f"完整辩论记录:\n{history}\n\n"
            "请给出最终的、最具体的、工程化的综合答案。"
            "整合各方最佳观点，解决分歧，给出可操作的方案。"
        )
        try:
            msg = self._anthropic.messages.create(
                model=self._api_model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            return f"[API综合失败: {e}]"

    def _format_round_summary(self, round_num: int, answers: dict[str, str], analysis: str) -> str:
        sep = "=" * 60
        lines = [sep, f"📋 第 {round_num} 轮结果", sep, ""]
        for name, ans in answers.items():
            preview = ans[:400] + ("..." if len(ans) > 400 else "")
            lines += [f"── {name} ──", preview, ""]
        lines += ["─" * 40, "🔍 API 分析：", analysis, sep]
        return "\n".join(lines)

    def run(self, question: str) -> SymposiumResult:
        all_rounds: list[RoundResult] = []
        names = [c.name for c in self.clients]

        # ── Round 0: pipeline send → parallel wait ─────────────────────────────
        self._log("⚗️  Round 0: 三家 AI 流水线发送 + 并行等待...")
        r0_answers = self._ask_all_pipeline(self.clients, question)

        analysis0 = self._api_summarize(question, r0_answers)
        r0 = RoundResult(round_num=0, answers=r0_answers)
        all_rounds.append(r0)

        summary0 = self._format_round_summary(0, r0_answers, analysis0)
        self._log("\n" + summary0)

        guidance = self._ask_user(
            summary0 + "\n\n作为裁判，请输入你的引导（直接回车跳过）:\n> "
        )
        r0.user_guidance = guidance
        if guidance:
            self._log(f"👤 用户引导: {guidance}")

        # ── Debate rounds ─────────────────────────────────────────────────────
        for rnd in range(1, self.debate_rounds + 1):
            self._log(f"\n⚔️  第 {rnd} 轮辩论...")
            prev_answers = all_rounds[-1].answers
            rnd_answers: dict[str, str] = {}

            # Build per-client challenge prompts (format rule prepended)
            challenge_prompts: dict[str, str] = {}
            for i, client in enumerate(self.clients):
                other = self.clients[(i + 1) % len(self.clients)]
                other_ans = prev_answers.get(other.name, "")
                # Summarize opponent's answer to key points before sending
                other_summary = self._summarize_for_challenge(other.name, other_ans)
                challenge = CHALLENGE_TMPL.format(
                    other_name=other.name,
                    other_answer=other_summary,
                )
                if guidance:
                    challenge = USER_GUIDANCE_TMPL.format(guidance=guidance) + "\n\n" + challenge
                challenge_prompts[client.name] = REPLY_FORMAT_RULE + challenge

            # Pipeline: send sequentially then poll all
            self._log(f"   流水线发送挑战...")
            for c in self.clients:
                self._log(f"   ✉️  发送给 {c.name}...")
                try:
                    c._type_and_send(challenge_prompts[c.name])
                except Exception as e:
                    self._log(f"   ⚠️  {c.name} 发送失败: {e}")

            self._log("   ⏳ 等待各方回复（轮询中）...")
            start_t = time.time()
            pending2 = {c.name: c for c in self.clients}
            while pending2 and (time.time() - start_t) < 900:
                for name in list(pending2.keys()):
                    c = pending2[name]
                    try:
                        if check_done(c._page, c.name):
                            ans = extract_reply_after_anchor(
                                c._page, c.name,
                                getattr(c, '_last_prompt', challenge_prompts[name]))
                            if not ans:
                                ans = c._wait_for_response()
                            rnd_answers[name] = ans
                            self._log(f"   ✓ {name} 完成 ({len(ans)} chars)")
                            del pending2[name]
                    except Exception as e:
                        self._log(f"   ⚠️  {name} 出错: {e}")
                if pending2:
                    self._log(f"   ⏳ 还在等: {list(pending2.keys())} ({int(time.time()-start_t)}s)")
                    time.sleep(15)

            analysis = self._api_summarize(question, rnd_answers)
            rr = RoundResult(round_num=rnd, answers=rnd_answers)
            all_rounds.append(rr)

            summary = self._format_round_summary(rnd, rnd_answers, analysis)
            self._log("\n" + summary)

            guidance = self._ask_user(
                summary + f"\n\n第{rnd}轮结束。请输入下一轮引导（直接回车继续）:\n> "
            )
            rr.user_guidance = guidance
            if guidance:
                self._log(f"👤 用户引导: {guidance}")

        # ── Synthesis ─────────────────────────────────────────────────────────
        self._log("\n✨ 最终综合（API）...")
        synthesis = self._api_synthesis(question, all_rounds)

        return SymposiumResult(
            question=question,
            rounds=all_rounds,
            synthesis=synthesis,
            synthesizer="Claude API",
        )
