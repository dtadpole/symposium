import anthropic
from .base import AIClient
from ..config import get_anthropic_key

MODEL = "claude-sonnet-4-6"


class ClaudeClient(AIClient):
    name = "Claude"

    def __init__(self):
        key = get_anthropic_key()
        if not key:
            raise RuntimeError("Anthropic API key not found")
        self._client = anthropic.Anthropic(api_key=key)

    def ask(self, prompt: str, system: str | None = None) -> str:
        kwargs = dict(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = system
        msg = self._client.messages.create(**kwargs)
        return msg.content[0].text.strip()
