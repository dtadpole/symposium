# 🏛 Symposium

> *"The truth is not owned by anyone at the table."*

**Symposium** is a multi-AI debate engine. Ask one question — Claude, GPT, and Gemini answer independently, then challenge each other's reasoning, and finally synthesize a single best answer.

Named after Plato's *Symposium*, where philosophers reclined and took turns arguing about the nature of love — not to win, but to get closer to truth.

---

## How it works

```
Your question
     │
     ▼
┌─────────────────────────────────┐
│  Round 0: All AIs answer        │  (concurrent)
│  Claude / GPT / Gemini          │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  Consensus Analysis             │
│  • What they agree on           │
│  • Where they diverge           │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  Debate (ring rotation)         │
│  Claude → challenges GPT        │
│  GPT    → challenges Gemini     │
│  Gemini → challenges Claude     │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  Final Synthesis                │
│  One definitive answer          │
└─────────────────────────────────┘
```

---

## Setup

```bash
pip install anthropic google-generativeai openai
```

Configure API keys in `~/.symposium.json`:
```json
{
  "openai_api_key": "sk-..."
}
```

Anthropic and Google keys are auto-read from OpenClaw's auth-profiles if available.

---

## Usage

```bash
# Ask directly
python -m symposium.main "Is consciousness purely computational?"

# Interactive mode
python -m symposium.main

# More debate rounds
python -m symposium.main "..." --rounds 2

# Skip saving to Obsidian
python -m symposium.main "..." --no-save
```

Results are automatically saved to Obsidian under `AI Chats/Symposium/`.

---

## Project structure

```
symposium/
├── main.py          # CLI entry point
├── config.py        # API key management
├── debate.py        # Core engine
├── output.py        # Terminal + Markdown output
└── clients/
    ├── base.py
    ├── claude.py
    ├── gemini.py
    └── gpt.py
```

---

MIT License
