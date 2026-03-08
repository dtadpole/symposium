"""
Symposium Debate Engine — 5-round structured debate.

Round structure:
  R1: 开篇立论  — position, definitions, 2 core arguments, key disputes
  R2: 聚焦质询  — 2-3 targeted questions on opponent's premises
  R3: 集中攻防  — respond + attack + defend + compress to 1-2 deciding questions
  R4: 焦点自由辩 — only established disputes, no new arguments
  R5: 总结陈词  — closing: why my side wins

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
    1: """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第1轮：开篇立论】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 本轮任务：
这是辩论的第一轮。你需要确立你方的立场和框架，为后续五轮辩论锁定赛道。
重点不在于铺陈所有论点，而在于说清楚"这场辩论应该按什么比、你方的核心主张是什么"。

📌 本轮辩题：
{question}

📝 本轮你需要完成（最多2个主论点，结构清晰）：
1. **明确立场**：你方对这个知识库设计问题的核心主张
2. **关键定义**：对辩题中最重要的概念给出你的定义
3. **判断标准**：应按什么标准评判哪个方案更好？
4. **2个核心论点**：支撑你立场的最重要的2个论点（不要多）
5. **核心焦点**：你认为本场真正的争议在哪？（1-2个）

⚠️ 规则：最多2个主论点，必须说清楚判断标准，为后续讨论锁定赛道。""",

    2: """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第2轮：聚焦质询】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 本轮任务：
这一轮的目的是逼对方把最关键的前提讲清楚。你要找到对方论点中最脆弱的地方，
用精准的问题揭示它的漏洞。不要东拉西扯，聚焦就是力量。

📨 对方（{other_name}）的完整开场立论原文如下：
────────────────────────────────────────
{other_full}
────────────────────────────────────────

📝 本轮你需要完成：
- 只针对对方的定义、标准、主论点提出 **2-3 个关键问题**
- 问题要短、准、集中——直指前提是否成立、逻辑是否闭合、适用范围是否过宽或过窄
- 每个问题后说明：为什么这是对方的薄弱点

⚠️ 规则：不允许新开论点，不允许跑到细枝末节。

最后用一句话总结：**我认为对方最脆弱的一点是**：""",

    3: """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第3轮：集中攻防】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 本轮任务：
这是全场最核心的回合。你需要同时做三件事：回应对方的质询、主动攻击对方的漏洞、
防守己方论点。最后把所有争议压缩到1-2个真正决定胜负的核心问题上。

📨 对方（{other_name}）上一轮的完整质询原文如下：
────────────────────────────────────────
{other_full}
────────────────────────────────────────

📝 本轮你需要完成：
1. **回应质询**：对方的问题暴露了你方哪些问题？逐一正面回应
2. **攻击对方**（最多2个点）：
   - 指出对方框架或主论点的关键漏洞
   - 说明"如果按对方逻辑，会导致什么具体问题"
3. **防守己方**：对方攻击了你方哪些核心论点？如何坚守？
4. **压缩争点**：把当前全部争议压缩成 **1-2 个决定胜负的核心问题**，明确说出来

⚠️ 规则：优先打最核心的点，不要同时开三个以上战场，每次发言对应明确争点。""",

    4: """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第4轮：焦点自由辩】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 本轮任务：
前三轮已经确定了核心争点。这一轮的目的是把它打透——不是发散，而是聚焦。
你只能在已经确认的争点上正面交锋，把你方的论证打到最实、最深。

📨 对方（{other_name}）上一轮的完整发言原文如下：
────────────────────────────────────────
{other_full}
────────────────────────────────────────

📝 本轮你需要完成：
- 明确说出：**我在回应哪一个核心争点**
- 正面回应对方在该争点上的最强论点
- 只允许：澄清误解、压实对方漏洞、强化己方比较优势

⚠️ 规则：禁止新增定义、禁止新开大论点、禁止引入无关新材料。把核心问题打透。""",

    5: """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【第5轮：总结陈词】—— 这是本场辩论最重要的一轮
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 本轮任务：
这是整场辩论的最后一轮，也是最重要的一轮。
你需要做两件事：
  第一，完整回顾这场辩论说了什么——让裁判清楚地知道双方讨论的全貌；
  第二，清晰说明你最终为什么认为应该这样设计这个系统——这是你的核心判断。

📨 对方（{other_name}）上一轮的完整发言原文如下：
────────────────────────────────────────
{other_full}
────────────────────────────────────────

📝 本轮你必须按以下结构完成（不引入任何新论点）：

**一、这场辩论我们说了什么**
回顾整场五轮辩论的核心内容：
- 双方分别主张什么？
- 双方在哪些核心问题上形成了真正的交锋？
- 哪些争点最终被解决了，哪些还存在分歧？

**二、我最终认为应该怎么做，以及为什么**
- 重申本方的判断标准：评判一个方案好坏的依据是什么？
- 在核心争点上，为什么本方的方案更好？具体说明
- 最终结论（一句话）：这个自我进化知识库系统应该怎么设计？
- 这个结论背后最根本的原因是什么？

⚠️ 规则：不引入新论点。总结陈词的目的是"让裁判看清全局、看清你方为什么赢"，而不是重复细节。""",
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
        debate_rounds: int = 5,
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

    def run(self, question: str, opening_context: str = "") -> SymposiumResult:
        all_rounds: list[RoundResult] = []
        prev_answers: dict[str, str] = {}
        user_guidance = ""

        # ── Send opening context to both AIs before Round 1 ──────────────────
        if opening_context:
            self._log("\n📋 发送开场设定给所有参与方...")
            setup_prompts = {c.name: opening_context for c in self.clients}
            self._send_all(setup_prompts)
            setup_answers = self._wait_all(hint_prompt=opening_context)
            self._log("   ✓ 开场设定已确认")
            # Store setup context for reference
            prev_answers = setup_answers  # so R1 can reference if needed

        for rnd in range(1, self.debate_rounds + 1):
            rname = ROUND_NAMES.get(rnd, f"第{rnd}轮")
            self._log(f"\n{'='*60}")
            self._log(f"⚔️  第{rnd}轮：{rname}")
            self._log("="*60)

            # Build prompts for each client
            prompts: dict[str, str] = {}
            for i, client in enumerate(self.clients):
                other = self.clients[(i + 1) % len(self.clients)]
                other_full = prev_answers.get(other.name, "（对方尚未发言）")

                template = ROUND_PROMPTS.get(rnd, ROUND_PROMPTS[5])
                p = template.format(
                    question=question,
                    other_name=other.name,
                    other_full=other_full,   # full original text, no summarization
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
