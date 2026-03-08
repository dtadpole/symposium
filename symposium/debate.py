"""
Core debate engine for Symposium.

Flow:
  1. Round 0  — All AIs answer the original question independently (concurrent)
  2. Consensus — Extract points all AIs agree on
  3. Debate    — For each point of disagreement, rotate: A challenges B, B challenges C, C challenges A
  4. Synthesis — One final AI synthesizes everything into a conclusive answer
"""

import concurrent.futures
from dataclasses import dataclass, field
from .clients.base import AIClient

# ── Prompts ────────────────────────────────────────────────────────────────────

CONSENSUS_SYSTEM = """You are a neutral analyst comparing multiple AI responses.
Identify what they genuinely agree on and where they meaningfully differ.
Be concise and specific. Output in this exact format:

CONSENSUS:
- <point 1>
- <point 2>
...

DISAGREEMENTS:
- <topic>: <AI_A's position> vs <AI_B's position> [vs <AI_C's position>]
...
"""

CHALLENGE_SYSTEM = """You are participating in a structured intellectual debate.
You will be shown another AI's answer to a question. Your job:
1. Identify the strongest points in their reasoning
2. Identify any flaws, gaps, or oversimplifications
3. Provide your own refined position

Be direct, intellectually honest, and rigorous. Aim to get closer to truth, not to win."""

SYNTHESIS_SYSTEM = """You are the final synthesizer in a multi-AI debate.
You have the original question, all initial answers, and the full debate transcript.
Your job: produce the single best answer possible — one that incorporates the strongest
points from all participants and resolves their disagreements with clear reasoning.
Be definitive. This is the final word."""


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class Round0Response:
    ai_name: str
    answer: str

@dataclass
class DebateExchange:
    challenger: str   # AI making the challenge
    defender: str     # AI being challenged
    topic: str
    challenge: str    # challenger's response to defender's position

@dataclass
class SymposiumResult:
    question: str
    round0: list[Round0Response]
    consensus_points: list[str]
    disagreement_topics: list[str]
    debate: list[DebateExchange]
    synthesis: str
    synthesizer: str


# ── Engine ─────────────────────────────────────────────────────────────────────

class SymposiumEngine:
    def __init__(self, clients: list[AIClient], debate_rounds: int = 1):
        if len(clients) < 2:
            raise ValueError("Need at least 2 AI clients")
        self.clients = clients
        self.debate_rounds = debate_rounds

    def _ask(self, client: AIClient, prompt: str, system: str | None = None) -> str:
        try:
            return client.ask(prompt, system=system)
        except Exception as e:
            return f"[Error from {client.name}: {e}]"

    def _ask_all_concurrent(self, prompt: str, system: str | None = None) -> list[tuple[str, str]]:
        """Ask all clients the same question concurrently. Returns [(name, answer), ...]"""
        with concurrent.futures.ThreadPoolExecutor() as ex:
            futures = {ex.submit(self._ask, c, prompt, system): c for c in self.clients}
            results = []
            for fut, client in futures.items():
                results.append((client.name, fut.result()))
        return results

    def _parse_consensus(self, analysis: str) -> tuple[list[str], list[str]]:
        """Parse the consensus analysis into (consensus_points, disagreement_topics)."""
        consensus, disagreements = [], []
        section = None
        for line in analysis.splitlines():
            line = line.strip()
            if line.upper().startswith("CONSENSUS"):
                section = "consensus"
            elif line.upper().startswith("DISAGREE"):
                section = "disagree"
            elif line.startswith("-") and section == "consensus":
                consensus.append(line[1:].strip())
            elif line.startswith("-") and section == "disagree":
                disagreements.append(line[1:].strip())
        return consensus, disagreements

    def run(self, question: str, verbose_callback=None) -> SymposiumResult:
        def log(msg):
            if verbose_callback:
                verbose_callback(msg)

        # ── Round 0: All answer independently ─────────────────────────────────
        log("⚗️  Round 0: asking all AIs independently...")
        r0_pairs = self._ask_all_concurrent(question)
        round0 = [Round0Response(name, answer) for name, answer in r0_pairs]

        # ── Find consensus & disagreements ─────────────────────────────────────
        log("🔍 Analyzing consensus and disagreements...")
        combined = "\n\n".join(
            f"=== {r.ai_name} ===\n{r.answer}" for r in round0
        )
        analysis_prompt = (
            f"Original question: {question}\n\n"
            f"Here are the answers from {len(round0)} AIs:\n\n{combined}"
        )
        # Use the first available client for analysis
        analysis = self._ask(self.clients[0], analysis_prompt, system=CONSENSUS_SYSTEM)
        consensus_points, disagreement_topics = self._parse_consensus(analysis)

        # ── Debate rounds ──────────────────────────────────────────────────────
        log(f"⚔️  Starting debate ({len(disagreement_topics)} disagreement(s))...")
        debate_exchanges: list[DebateExchange] = []

        # Build a lookup: ai_name → Round0 answer
        r0_by_name = {r.ai_name: r.answer for r in round0}

        for _round in range(self.debate_rounds):
            for i, client in enumerate(self.clients):
                # This client challenges the next client in the ring
                defender_client = self.clients[(i + 1) % len(self.clients)]
                defender_answer = r0_by_name.get(defender_client.name, "")

                challenge_prompt = (
                    f"Original question: {question}\n\n"
                    f"{defender_client.name} answered:\n{defender_answer}\n\n"
                    f"Known disagreements:\n" +
                    "\n".join(f"- {d}" for d in disagreement_topics) +
                    f"\n\nChallenge {defender_client.name}'s answer from your own perspective."
                )
                log(f"  {client.name} → challenges {defender_client.name}...")
                challenge = self._ask(client, challenge_prompt, system=CHALLENGE_SYSTEM)

                exchange = DebateExchange(
                    challenger=client.name,
                    defender=defender_client.name,
                    topic=f"Round {_round+1}",
                    challenge=challenge,
                )
                debate_exchanges.append(exchange)
                # Update defender's "current position" with challenger's critique
                r0_by_name[defender_client.name] = (
                    f"{defender_answer}\n\n[After challenge from {client.name}]:\n{challenge}"
                )

        # ── Synthesis ──────────────────────────────────────────────────────────
        log("✨ Synthesizing final answer...")
        debate_transcript = "\n\n".join(
            f"--- {ex.challenger} challenges {ex.defender} ---\n{ex.challenge}"
            for ex in debate_exchanges
        )
        synthesis_prompt = (
            f"Original question: {question}\n\n"
            f"=== Initial Answers ===\n{combined}\n\n"
            f"=== Consensus Points ===\n" +
            "\n".join(f"- {p}" for p in consensus_points) +
            f"\n\n=== Debate Transcript ===\n{debate_transcript}\n\n"
            f"Now provide the definitive synthesized answer."
        )
        # Last client does the synthesis (rotation ensures variety)
        synthesizer = self.clients[-1]
        synthesis = self._ask(synthesizer, synthesis_prompt, system=SYNTHESIS_SYSTEM)

        return SymposiumResult(
            question=question,
            round0=round0,
            consensus_points=consensus_points,
            disagreement_topics=disagreement_topics,
            debate=debate_exchanges,
            synthesis=synthesis,
            synthesizer=synthesizer.name,
        )
