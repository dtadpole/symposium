"""
Base class for Playwright-based AI chat clients.
Each client manages one browser page, maintains conversation context,
and implements the same AIClient interface.
"""

import time
from playwright.sync_api import Page
from ..base import AIClient


class PlaywrightChatClient(AIClient):
    """Abstract base: a single browser page that stays open across turns."""

    start_url: str = ""
    name: str = "unknown"

    def __init__(self, page: Page):
        self._page = page
        self._initialized = False

    def _init_conversation(self):
        """Navigate to start URL and wait for the chat input to be ready."""
        raise NotImplementedError

    def _type_and_send(self, text: str):
        """Type text into the chat input and submit."""
        raise NotImplementedError

    def _wait_for_response(self) -> str:
        """Wait for the AI to finish responding and return the response text."""
        raise NotImplementedError

    def ask(self, prompt: str, system: str | None = None) -> str:
        if not self._initialized:
            self._init_conversation()
            self._initialized = True

        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        self._type_and_send(full_prompt)
        return self._wait_for_response()
