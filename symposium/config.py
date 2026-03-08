"""
Config — API key loader.
Reads from ~/.openclaw/agents/main/agent/auth-profiles.json first,
then falls back to environment variables.
"""

import json
import os
from pathlib import Path


def _load_profiles() -> dict:
    p = Path.home() / ".openclaw/agents/main/agent/auth-profiles.json"
    if p.exists():
        return json.loads(p.read_text()).get("profiles", {})
    return {}


def get_anthropic_key() -> str | None:
    prof = _load_profiles().get("anthropic:default", {})
    return prof.get("token") or prof.get("key") or os.getenv("ANTHROPIC_API_KEY")


def get_google_key() -> str | None:
    prof = _load_profiles().get("google:default", {})
    return prof.get("key") or prof.get("token") or os.getenv("GOOGLE_API_KEY")


def get_openai_key() -> str | None:
    # Not in profiles yet — read from env or ~/.symposium.json
    cfg = Path.home() / ".symposium.json"
    if cfg.exists():
        data = json.loads(cfg.read_text())
        key = data.get("openai_api_key")
        if key:
            return key
    return os.getenv("OPENAI_API_KEY")


def available_clients() -> list[str]:
    clients = []
    if get_anthropic_key():
        clients.append("claude")
    if get_google_key():
        clients.append("gemini")
    if get_openai_key():
        clients.append("gpt")
    return clients
