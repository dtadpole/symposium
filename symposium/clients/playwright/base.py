"""
Base class for Playwright-based AI chat clients.
Each client manages one browser page, maintains conversation context,
and implements the same AIClient interface.

Critical rule:
Before every message, the client must try to enforce:
1. latest available model
2. strongest / longest thinking mode
"""

from playwright.sync_api import Page
from ..base import AIClient
from .ui_agent import scan_ui, choose_action, recover_page, focus_best_input


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

    def ensure_best_config(self):
        """Ensure latest model + strongest thinking mode before sending.
        Override per platform.
        """
        return None

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

        # Mandatory: try to choose newest model + strongest thinking mode
        self.ensure_best_config()

        # Recover page back to input-ready state if menus / overlays remain.
        ui = scan_ui(self._page)
        if ui.get("overlays"):
            recover_page(self._page, ui)
        else:
            focus_best_input(self._page, ui)

        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        self._type_and_send(full_prompt)
        return self._wait_for_response()
