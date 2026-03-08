"""Abstract base class for all AI clients."""

from abc import ABC, abstractmethod


class AIClient(ABC):
    name: str = "unknown"

    @abstractmethod
    def ask(self, prompt: str, system: str | None = None) -> str:
        """Send a prompt and return the response text."""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"
