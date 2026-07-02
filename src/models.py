"""Typed models for the HTTP contract and internal data structures.

Design choice: the *API envelope* (request/response bodies) is strictly typed
with Pydantic so malformed judge calls fail loudly and we always return the
exact response schema. The *context payloads* (category / merchant / trigger /
customer) are kept as permissive dicts because the judge injects new, unseen
fields mid-test (Phase 3 — adaptive context injection). Hard-typing them would
make the bot brittle to exactly the thing it is rewarded for adapting to.

Accessors that need a field reach for it defensively via `context_engine`
helpers instead of relying on a frozen schema.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# CTA / send-as vocabularies                                                   #
# --------------------------------------------------------------------------- #
CTA_BINARY_YES = "binary_yes_no"
CTA_BINARY_CONFIRM = "binary_confirm_cancel"
CTA_OPEN = "open_ended"
CTA_MULTI_SLOT = "multi_choice_slot"
CTA_NONE = "none"

SEND_AS_VERA = "vera"
SEND_AS_MERCHANT = "merchant_on_behalf"


# --------------------------------------------------------------------------- #
# /v1/context                                                                 #
# --------------------------------------------------------------------------- #
class ContextPush(BaseModel):
    scope: Literal["category", "merchant", "customer", "trigger"]
    context_id: str
    version: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)
    delivered_at: Optional[str] = None


class ContextAck(BaseModel):
    accepted: bool
    ack_id: Optional[str] = None
    stored_at: Optional[str] = None
    reason: Optional[str] = None
    current_version: Optional[int] = None
    details: Optional[str] = None


# --------------------------------------------------------------------------- #
# /v1/tick                                                                     #
# --------------------------------------------------------------------------- #
class TickRequest(BaseModel):
    now: Optional[str] = None
    available_triggers: list[str] = Field(default_factory=list)


class Action(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    send_as: str = SEND_AS_VERA
    trigger_id: Optional[str] = None
    template_name: str = "vera_generic_v1"
    template_params: list[str] = Field(default_factory=list)
    body: str
    cta: str = CTA_OPEN
    suppression_key: str = ""
    rationale: str = ""


class TickResponse(BaseModel):
    actions: list[Action] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# /v1/reply                                                                    #
# --------------------------------------------------------------------------- #
class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str = ""
    received_at: Optional[str] = None
    turn_number: int = 0


class ReplyResponse(BaseModel):
    action: Literal["send", "wait", "end"]
    body: Optional[str] = None
    cta: Optional[str] = None
    wait_seconds: Optional[int] = None
    rationale: str = ""


# --------------------------------------------------------------------------- #
# Internal: the resolved, fully-hydrated composition request                  #
# --------------------------------------------------------------------------- #
class ComposedMessage(BaseModel):
    """The canonical output of the composer (mirrors the challenge contract)."""
    body: str
    cta: str = CTA_OPEN
    send_as: str = SEND_AS_VERA
    suppression_key: str = ""
    rationale: str = ""
    template_name: str = "vera_generic_v1"
    template_params: list[str] = Field(default_factory=list)
    # Bookkeeping the API layer uses but the JSONL submission ignores.
    levers: list[str] = Field(default_factory=list)
    facts_used: list[str] = Field(default_factory=list)
    deliverable: str = ""        # noun phrase used to seed grammatical follow-ups
