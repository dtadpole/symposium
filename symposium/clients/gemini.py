from google import genai
from .base import AIClient
from ..config import get_google_key

MODEL = "gemini-2.5-flash"


class GeminiClient(AIClient):
    name = "Gemini"

    def __init__(self):
        key = get_google_key()
        if not key:
            raise RuntimeError("Google API key not found")
        self._client = genai.Client(api_key=key)

    def ask(self, prompt: str, system: str | None = None) -> str:
        if system:
            prompt = f"{system}\n\n{prompt}"
        resp = self._client.models.generate_content(model=MODEL, contents=prompt)
        return resp.text.strip()
