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

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import json
import subprocess
import tempfile
import urllib.request
import urllib.error
import anthropic
import yaml

from .clients.base import AIClient
from .clients.playwright.chatgpt import ATTACHMENT_MARKER
from .clients.playwright.response_waiter import check_done, extract_reply_after_anchor

# ── Load config files ──────────────────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).parent / "config"

def _load_yaml(name: str) -> dict:
    path = _CONFIG_DIR / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

_FORMAT_CFG   = _load_yaml("format.yaml")
_CONTENT_CFG  = _load_yaml("content.yaml")
_PERSONA_CFG  = _load_yaml("persona.yaml")


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


# ── Constants loaded from config ──────────────────────────────────────────────

REPLY_FORMAT_RULE   = _FORMAT_CFG.get("reply_format_rule", "") + "\n"

# Assemble opening context: persona header + content body
_persona_identity = _PERSONA_CFG.get("debater_identity", "")
_persona_rules    = _PERSONA_CFG.get("debater_rules", [])
_judge_desc       = _PERSONA_CFG.get("judge_description", "")
_rules_text       = "\n".join(f"- {r}" for r in _persona_rules)
_persona_block    = (
    f"{_persona_identity}\n"
    f"辩手准则：\n{_rules_text}\n\n"
    f"{_judge_desc}\n"
)
OPENING_CONTEXT       = _persona_block + _CONTENT_CFG.get("opening_context", "")
DEFAULT_QUESTION      = _CONTENT_CFG.get("question", "")
ROUND_NAMES: dict     = {int(k): v for k, v in _FORMAT_CFG.get("round_names", {}).items()}
ROUND_PROMPTS: dict   = {int(k): v for k, v in _FORMAT_CFG.get("round_prompts", {}).items()}
DEFAULT_ROUNDS: int   = int(_FORMAT_CFG.get("debate_rounds", 5))
JUDGE_IDENTITY        = _PERSONA_CFG.get("judge_identity", "")
JUDGE_EVAL_PROMPT     = _PERSONA_CFG.get("judge_evaluation_prompt", "")
_CALLBACK_CFG         = _PERSONA_CFG.get("callback", {})




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

    def _build_transcript(self, all_rounds: list[RoundResult]) -> str:
        """Build full debate transcript for judge evaluation."""
        parts = []
        for r in all_rounds:
            parts.append(f"{'━'*40}\n【第{r.round_num}轮：{r.round_name}】\n{'━'*40}")
            for name, answer in r.answers.items():
                parts.append(f"\n── {name} ──\n{answer}")
            if r.user_guidance:
                parts.append(f"\n【裁判引导】{r.user_guidance}")
        return "\n\n".join(parts)

    def _judge_evaluation(self, question: str, all_rounds: list[RoundResult]) -> str:
        """Run independent judge evaluation using OpenClaw's API model."""
        if not self._anthropic:
            return "[无法进行裁判评判：API key 未配置]"
        transcript = self._build_transcript(all_rounds)
        system = JUDGE_IDENTITY.strip()
        prompt = JUDGE_EVAL_PROMPT.format(debate_transcript=transcript)
        try:
            self._log("\n🏛️  裁判评判中（API）...")
            msg = self._anthropic.messages.create(
                model=self._api_model,
                max_tokens=4000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            return f"[裁判评判失败: {e}]"

    def _callback_to_parent(self, result_path: str, summary: str) -> bool:
        """Notify parent agent (OpenClaw/Blue Lantern) that debate is complete.

        Symposium does NOT send to users directly. Instead, it calls back to the
        OpenClaw main agent via `openclaw agent --channel last --deliver`.
        The parent agent determines the correct channel (Telegram / WhatsApp / etc.)
        and routes the result to the user.

        Args:
            result_path: path to the full debate result markdown file
            summary:     short notification text for the parent agent
        """
        if not _CALLBACK_CFG.get("enabled", False):
            return False
        try:
            args = _CALLBACK_CFG.get("openclaw_agent_args", "--channel last --deliver")
            cmd = ["openclaw", "agent"] + args.split() + ["--message", summary]
            self._log(f"📡  回传给 OpenClaw parent agent: {' '.join(cmd[:5])}...")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode == 0:
                self._log("✅  Parent agent 已收到辩论结果，将自动路由给用户")
                return True
            else:
                self._log(f"⚠️  Parent agent 回传失败 (rc={proc.returncode}): {proc.stderr[:200]}")
                return False
        except Exception as e:
            self._log(f"⚠️  Parent agent 回传异常: {e}")
            return False

    def _save_round_content(self, round_num: int, client_name: str, content: str) -> str:
        """Persist a debater's full response to a file. Returns the file path."""
        output_dir = Path(_CALLBACK_CFG.get("output_dir", "~/Symposium/output")).expanduser()
        round_dir = output_dir / "rounds"
        round_dir.mkdir(parents=True, exist_ok=True)
        fname = round_dir / f"R{round_num}_{client_name.replace(' ', '_')}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(content)
        return str(fname)

    def _send_all(self, prompts: dict[str, str], prev_answers: dict[str, str] = None,
                  round_num: int = 0):
        """Pipeline: send to all clients sequentially (fast).

        For ChatGPT: opponent's full text (Claude's response) is:
          1. Saved to a persistent file (~/Symposium/output/rounds/R{n}_Claude.txt)
          2. Uploaded as .txt file attachment to ChatGPT
          3. If upload fails → include full text inline (no data loss)
        For Claude: ClipboardEvent paste already displays long text as a block.
        """
        for c in self.clients:
            prompt = prompts.get(c.name, "")
            full = REPLY_FORMAT_RULE + prompt
            self._log(f"   ✉️  发送给 {c.name}...")
            try:
                # For ChatGPT: upload opponent content as file attachment
                if c.name == "ChatGPT" and "────────────────────────────────────────" in full:
                    m = re.search(
                        r'────────────────────────────────────────\n(.*?)\n────────────────────────────────────────',
                        full, re.DOTALL
                    )
                    if m:
                        attachment_content = m.group(1).strip()
                        char_count = len(attachment_content)
                        self._log(f"   📎 对方原文 {char_count} 字符，准备上传附件...")

                        # Save to persistent file first (guarantee no data loss)
                        saved_path = self._save_round_content(round_num, "Claude", attachment_content)
                        self._log(f"   💾 已持久化: {saved_path}")

                        # Replace block with reference + ATTACHMENT_MARKER for upload
                        main_text = re.sub(
                            r'────────────────────────────────────────\n.*?\n────────────────────────────────────────',
                            '（对方完整发言见附件 opponent_argument.txt）',
                            full, flags=re.DOTALL
                        )
                        full = main_text + f"\n\n{ATTACHMENT_MARKER}\n" + attachment_content
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
            self._send_all(prompts, round_num=rnd)
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

        # Judge evaluation (independent API call with judge persona — not Claude/ChatGPT web UI)
        judgment = self._judge_evaluation(question, all_rounds)

        # Save full result to output file
        output_dir = Path(_CALLBACK_CFG.get("output_dir", "~/Symposium/output")).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        result_path = output_dir / f"debate_{ts}.md"
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(f"# Symposium 辩论结果\n\n")
            f.write(f"**议题：** {question}\n\n")
            f.write(f"---\n\n## 裁判评判\n\n{judgment}\n\n")
            f.write(f"---\n\n## 综合建议\n\n{synthesis}\n\n")
            f.write(f"---\n\n## 完整辩论记录\n\n{self._build_transcript(all_rounds)}\n")
        self._log(f"💾  辩论结果已保存: {result_path}")

        # Callback to parent agent (OpenClaw/Blue Lantern) — parent routes to user's channel
        callback_msg = (
            f"Symposium 辩论结束。议题：{question[:60]}...\n\n"
            f"完整结果文件：{result_path}\n\n"
            f"【裁判评判摘要】\n{judgment[:1500]}\n\n"
            f"【综合建议摘要】\n{synthesis[:800]}"
        )
        self._callback_to_parent(str(result_path), callback_msg)

        return SymposiumResult(
            question=question,
            rounds=all_rounds,
            synthesis=synthesis,
            synthesizer="Claude API",
        )
