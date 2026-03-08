from openai import OpenAI
from .base import AIClient
from ..config import get_openai_key

MODEL = "gpt-4o"


class GPTClient(AIClient):
    name = "GPT"

    def __init__(self):
        key = get_openai_key()
        if not key:
            raise RuntimeError("OpenAI API key not found")
        self._client = OpenAI(api_key=key)

    def ask(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=2048,
        )
        return resp.choices[0].message.content.strip()
