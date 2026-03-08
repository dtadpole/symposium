"""
Symposium Debate Engine — 6-round structured debate.

Round structure:
  0 (R1): Opening statement — position, 2 core arguments, key disputes
  1 (R2): Focused questioning — 2-3 questions on opponent's premises
  2 (R3): Framework attack — challenge definitions and judgment standards
  3 (R4): Substantive attack — attack core arguments, compress to 1-2 deciding questions
  4 (R5): Focused free debate — only on established core disputes
  5 (R6): Closing statement — why my side wins

Architecture:
  - 2 browser clients opened ONCE, kept alive throughout
  - Pipeline: send sequentially (fast), poll all pages until done
  - Analysis/summary via Anthropic API
  - Opponent answer summarized to key points before sending as challenge
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from .clients.base import AIClient
from .clients.playwright.response_waiter import check_done, extract_reply_after_anchor


def _load_openclaw_anthropic() -> tuple[str | None, str]:
    import os, json as _json
    from pathlib import Path as _Path
    key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    try:
        p = _Path.home() / ".openclaw/agents/main/agent/auth-profiles.json"
        data = _json.loads(p.read_text())
        key = key or data["profiles"]["anthropic:default"]["token"]
        model = data.get("defaultModel", model).replace("anthropic/", "")
    except Exception:
        pass
    return key, model


# ── Format rule prepended to every AI prompt ──────────────────────────────────

REPLY_FORMAT_RULE = (
    "【格式要求】请将你的完整回答直接写在对话主回复中，"
    "不要创建任何文档、artifact 或附件。所有内容必须在这条消息的正文里。\n\n"
)

# ── Round prompts ──────────────────────────────────────────────────────────────

ROUND_PROMPTS = {
    1: """【第1轮：开篇立论】

辩题：{question}

请完成以下内容（最多2个主论点）：

1. **明确立场**：你方的核心主张是什么？
2. **关键定义**：对本题中最重要的概念给出你的定义
3. **判断标准**：你认为应按什么标准判断这场辩论的输赢？
4. **2个核心论点**：支撑你立场的最重要的2个论点
5. **核心焦点**：你认为本场真正的争议焦点是什么？（1-2个）

规则：最多2个主论点，必须说清楚判断标准和本场焦点。""",

    2: """【第2轮：聚焦质询】

对方（{other_name}）的立论要点：
{other_summary}

请只针对对方的定义、标准、主论点提出 2-3 个关键问题：
- 问题必须短、准、集中——直指前提是否成立、逻辑是否闭合、适用范围是否过宽或过窄
- 不允许新开论点，不允许跑到细枝末节
- 每个问题后说明为什么这是对方的薄弱点

最后一句话总结：**我认为对方最脆弱的一点是**：""",

    3: """【第3轮：集中攻防】

对方（{other_name}）上一轮的核心论点：
{other_summary}

本轮是全场核心回合，直接处理最重要的争议：

1. **回应质询**：对方的质询暴露了你方哪些问题？如何回应？
2. **攻击对方**（最多2个点）：
   - 指出对方框架或主论点的关键漏洞
   - 说明"如果按对方逻辑，会导致什么问题"
3. **防守己方**：对方攻击了你方哪些点？如何坚守？
4. **压缩争点**：把全场争议压缩成 1-2 个真正决定胜负的核心问题

规则：优先打最核心的点，不要同时开三个以上战场，每次发言对应明确争点。""",

    4: """【第4轮：焦点自由辩】

对方（{other_name}）在集中攻防中确认的核心论点：
{other_summary}

本轮只围绕前一轮已确认的 1-2 个核心争点正面交锋：

- 只讨论已经确认的核心争点，禁止新增定义、新开大论点、引入无关新材料
- 每次发言明确说：我在回应哪一个争点
- 只允许：澄清误解、压实对方漏洞、强化己方比较优势

把前面确定的核心问题打透，不是发散。""",

    5: """【第5轮：总结陈词】

请做最终总结——不引入新论点，只总结决定性部分。

结构（必须按此顺序）：
1. **这场辩论比什么**：重申本方的判断标准
2. **双方争在哪**：回顾本场最终留下来的核心争点（1-2个）
3. **为什么我方赢**：在这些争点上，本方为什么占优？具体说明
4. **最终结论**（一句话）：这个自我演化知识库系统应该怎么做？

这是判决书，不是重复，要有清晰的胜负逻辑。""",
}


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class RoundResult:
    round_num: int
    round_name: str
    answers: dict[str, str]
    user_guidance: str = ""

@dataclass
class SymposiumResult:
    question: str
    rounds: list[RoundResult]
    synthesis: str
    synthesizer: str


# ── Engine ─────────────────────────────────────────────────────────────────────

ROUND_NAMES = {
    1: "开篇立论",
    2: "聚焦质询",
    3: "集中攻防",
    4: "焦点自由辩",
    5: "总结陈词",
}

POLL_INTERVAL = 15
HARD_TIMEOUT = 900


class SymposiumEngine:
    def __init__(
        self,
        clients: list[AIClient],
        api_key: str | None = None,
        debate_rounds: int = 6,
        user_input_fn: Callable[[str], str] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ):
        if len(clients) < 2:
            raise ValueError("Need at least 2 AI clients")
        self.clients = clients
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

    def _api_call(self, prompt: str, max_tokens: int = 800) -> str:
        if not self._anthropic:
            return ""
        try:
            msg = self._anthropic.messages.create(
                model=self._api_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            return f"[API error: {e}]"

    def _summarize_for_challenge(self, name: str, answer: str) -> str:
        """Summarize one AI's answer to 3-5 key points for use in challenge prompt."""
        if not self._anthropic or len(answer) < 600:
            return answer
        prompt = (
            f"以下是 {name} 在辩论中的发言，请提炼出最核心的3-5个论点，"
            f"每点1-2句话，保留关键概念和具体主张，去掉重复和铺垫：\n\n{answer}"
        )
        try:
            msg = self._anthropic.messages.create(
                model=self._api_model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception:
            return answer[:1200]

    def _api_round_analysis(self, round_num: int, answers: dict[str, str]) -> str:
        combined = "\n\n".join(f"=== {n} ===\n{a}" for n, a in answers.items())
        prompt = (
            f"第{round_num}轮辩论（{ROUND_NAMES.get(round_num, '')}）各方发言：\n\n{combined}\n\n"
            "请简要分析：\n"
            "1. 本轮双方的核心交锋点\n"
            "2. 目前谁的论证更有力？为什么？\n"
            "3. 还有哪些关键问题未解决？\n"
            "保持简洁，每点不超过2句。"
        )
        return self._api_call(prompt, max_tokens=600)

    def _api_synthesis(self, question: str, all_rounds: list[RoundResult]) -> str:
        history = "\n\n".join(
            f"=== {r.round_name}（第{r.round_num}轮）===\n" +
            "\n".join(f"-- {n} --\n{a}" for n, a in r.answers.items())
            for r in all_rounds
        )
        prompt = (
            f"辩题：{question}\n\n完整辩论记录：\n{history}\n\n"
            "请给出最终综合答案：\n"
            "1. 整合双方最佳观点\n"
            "2. 解决核心分歧，给出明确立场\n"
            "3. 具体、可操作的最终方案\n"
            "4. 这个方案为什么比任何单方的方案更好"
        )
        return self._api_call(prompt, max_tokens=3000)

    def _send_all(self, prompts: dict[str, str]):
        """Pipeline: send to all clients sequentially (fast)."""
        for c in self.clients:
            full = REPLY_FORMAT_RULE + prompts.get(c.name, "")
            self._log(f"   ✉️  发送给 {c.name}...")
            try:
                c._type_and_send(full)
            except Exception as e:
                self._log(f"   ⚠️  {c.name} 发送失败: {e}")

    def _wait_all(self, hint_prompt: str = "") -> dict[str, str]:
        """Poll all pages until each is done."""
        self._log("   ⏳ 等待各方回复（轮询中）...")
        start = time.time()
        pending = {c.name: c for c in self.clients}
        results: dict[str, str] = {}

        while pending and (time.time() - start) < HARD_TIMEOUT:
            for name in list(pending.keys()):
                c = pending[name]
                try:
                    if check_done(c._page, c.name):
                        ans = extract_reply_after_anchor(c._page, c.name,
                                                         getattr(c, '_last_prompt', hint_prompt))
                        if not ans:
                            ans = c._wait_for_response()
                        results[name] = ans
                        self._log(f"   ✓ {name} 完成 ({len(ans)} chars)")
                        del pending[name]
                except Exception as e:
                    self._log(f"   ⚠️  {name} 轮询出错: {e}")
            if pending:
                elapsed = int(time.time() - start)
                self._log(f"   ⏳ 还在等: {list(pending.keys())} ({elapsed}s)")
                time.sleep(POLL_INTERVAL)

        for name, c in pending.items():
            self._log(f"   ⏰ {name} 超时，强制提取...")
            try:
                results[name] = c._wait_for_response()
            except Exception as e:
                results[name] = f"[{name} timeout: {e}]"
        return results

    def _ask_user(self, display: str) -> str:
        if self.user_input_fn:
            return self.user_input_fn(display)
        return ""

    def _format_round_display(self, rnd: int, name: str, answers: dict[str, str], analysis: str) -> str:
        sep = "=" * 60
        lines = [sep, f"📋 第{rnd}轮：{name}", sep]
        for ai, ans in answers.items():
            preview = ans[:500] + ("..." if len(ans) > 500 else "")
            lines += [f"\n── {ai} ──", preview]
        lines += ["\n─── API 分析 ───", analysis, sep]
        return "\n".join(lines)

    def run(self, question: str) -> SymposiumResult:
        all_rounds: list[RoundResult] = []
        names = [c.name for c in self.clients]
        prev_answers: dict[str, str] = {}
        user_guidance = ""

        for rnd in range(1, self.debate_rounds + 1):
            rname = ROUND_NAMES.get(rnd, f"第{rnd}轮")
            self._log(f"\n{'='*60}")
            self._log(f"⚔️  第{rnd}轮：{rname}")
            self._log("="*60)

            # Build prompts for each client
            prompts: dict[str, str] = {}
            for i, client in enumerate(self.clients):
                other = self.clients[(i + 1) % len(self.clients)]
                other_ans = prev_answers.get(other.name, "")
                other_summary = self._summarize_for_challenge(other.name, other_ans) if other_ans else ""

                template = ROUND_PROMPTS.get(rnd, ROUND_PROMPTS[5])
                p = template.format(
                    question=question,
                    other_name=other.name,
                    other_summary=other_summary,
                )
                if user_guidance:
                    p = f"【裁判引导】{user_guidance}\n\n" + p
                prompts[client.name] = p

            # Send all + wait all
            self._send_all(prompts)
            answers = self._wait_all()

            # API round analysis
            analysis = self._api_round_analysis(rnd, answers)
            rr = RoundResult(round_num=rnd, round_name=rname, answers=answers)
            all_rounds.append(rr)
            prev_answers = answers

            # Display + user input
            display = self._format_round_display(rnd, rname, answers, analysis)
            self._log("\n" + display)

            if rnd < self.debate_rounds:
                guidance = self._ask_user(
                    display + f"\n\n第{rnd}轮结束。请输入裁判引导（直接回车继续）:\n> "
                )
                rr.user_guidance = guidance
                if guidance:
                    user_guidance = guidance
                    self._log(f"👤 裁判引导: {guidance}")

        # Final synthesis
        self._log("\n✨ 最终综合（API）...")
        synthesis = self._api_synthesis(question, all_rounds)

        return SymposiumResult(
            question=question,
            rounds=all_rounds,
            synthesis=synthesis,
            synthesizer="Claude API",
        )
