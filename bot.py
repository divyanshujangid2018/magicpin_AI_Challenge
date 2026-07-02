"""bot.py — the submission contract entrypoint.

Two ways to use this file:

  1. As the challenge `compose(...)` module (challenge-brief.md §7.1):

         from bot import compose
         result = compose(category, merchant, trigger, customer)
         # -> {"body", "cta", "send_as", "suppression_key", "rationale"}

  2. As the ASGI app for the testing harness (challenge-testing-brief.md):

         uvicorn bot:app --host 0.0.0.0 --port 8080

`compose` is pure and deterministic (LLM polish is off unless VERA_USE_LLM=1),
so it can be used offline to regenerate `submission.jsonl` reproducibly.
"""
from __future__ import annotations

from typing import Optional

from src.app import app  # noqa: F401  (exposes `bot:app` for uvicorn)
from src.composer import Composer
from src.context_engine import Resolver

_composer = Composer()


def compose(category: dict, merchant: dict, trigger: dict,
            customer: Optional[dict] = None) -> dict:
    """Compose a single message from the 4 contexts (dicts from the dataset)."""
    rc = Resolver(category or {}, merchant or {}, trigger or {}, customer).resolve()
    msg = _composer.compose(rc)
    return {
        "body": msg.body,
        "cta": msg.cta,
        "send_as": msg.send_as,
        "suppression_key": msg.suppression_key,
        "rationale": msg.rationale,
        "levers": msg.levers,
    }
