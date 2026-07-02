"""conversation_handlers.py — optional multi-turn entrypoint (tiebreaker).

Implements `respond(state, merchant_message)` per challenge-brief.md §7.4. Wraps
the stateful ConversationEngine so a single-shot caller can drive a replay
without the HTTP server.

`state` is a dict like:
    {
      "conversation_id": "conv_x",
      "merchant_id": "m_001_...",
      "customer_id": null,
      "trigger_id": "trg_001_...",
      "turn_number": 2,
      "history": [{"from": "vera"|"merchant", "body": "..."}],   # optional
      "contexts": {                                              # optional, offline use
          "category": {...}, "merchant": {...},
          "trigger": {...}, "customer": {...}
      }
    }
"""
from __future__ import annotations

from typing import Any

from src.context_engine import ContextStore
from src.conversation_engine import ConversationEngine
from src.trigger_engine import TriggerEngine

_store = ContextStore()
_triggers = TriggerEngine(_store)
_engine = ConversationEngine(_store, _triggers)


def _prime(state: dict) -> None:
    """Load any inline contexts (for offline/standalone replay) and seed history."""
    ctx = state.get("contexts") or {}
    for scope, cid_key in (("category", "slug"), ("merchant", "merchant_id"),
                           ("trigger", "id"), ("customer", "customer_id")):
        payload = ctx.get(scope)
        if payload:
            cid = payload.get(cid_key) or state.get(f"{scope}_id") or scope
            _store.put(scope, cid, 999, payload)

    conv_id = state.get("conversation_id", "conv")
    history = state.get("history") or []
    last_bot = next((h.get("body", "") for h in reversed(history)
                     if h.get("from") == "vera"), "")
    # derive a grammatical deliverable noun from the trigger kind for follow-ups
    from src.engagement_engine import KIND_DELIVERABLE
    trig = (state.get("contexts") or {}).get("trigger") or {}
    deliverable = KIND_DELIVERABLE.get(trig.get("kind", ""), "the next step")
    offer = ""
    merch = (state.get("contexts") or {}).get("merchant") or {}
    active = [o for o in merch.get("offers", []) or [] if o.get("status") == "active"]
    if active:
        offer = active[0].get("title", "")
    _engine.register(conv_id, state.get("merchant_id", ""), state.get("customer_id"),
                     state.get("trigger_id"), last_bot,
                     prepared_action=deliverable, offer_title=offer)


def respond(state: dict, merchant_message: str) -> dict[str, Any]:
    """Given the conversation so far + the merchant's latest message, reply."""
    _prime(state)
    resp = _engine.handle_reply(
        conversation_id=state.get("conversation_id", "conv"),
        merchant_id=state.get("merchant_id", ""),
        message=merchant_message,
        turn_number=int(state.get("turn_number", 1)),
    )
    return resp.model_dump(exclude_none=True)
