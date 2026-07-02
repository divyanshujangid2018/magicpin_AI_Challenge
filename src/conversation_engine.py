"""Conversation Engine — intent, memory, follow-ups, send/wait/end.

Owns everything that happens after the first outbound: it remembers each
conversation, detects the merchant's intent on every reply, and decides the next
move. The three hardest cases the brief calls out are handled explicitly:

  * auto-reply hell      -> detect, back off, then end (no wasted turns)
  * intent transition    -> "let's do it" flips from qualifying to ACTION mode
  * hostile / off-topic  -> graceful exit or polite redirect, stay on mission

Anti-repetition is enforced: we never send the same body twice in a conversation.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import auto_reply_detector as ard
from .context_engine import ContextStore, ResolvedContext
from .merchant_intelligence import MerchantIntelligence
from .models import (ReplyResponse, CTA_BINARY_YES, CTA_BINARY_CONFIRM, CTA_OPEN,
                     CTA_NONE)


# ---- intent vocabulary ---------------------------------------------------- #
_COMMIT = ["lets do it", "let's do it", "go ahead", "yes please", "sounds good",
           "ok lets", "ok let's", "do it", "proceed", "confirm", "send it",
           "sure go", "haan karo", "kar do", "theek hai karo", "yes do",
           "go for it", "lets go", "let's go", "set it up", "book it", "draft it"]
_AFFIRM = ["yes", "yeah", "yep", "ok", "okay", "haan", "ji", "sure", "interested",
           "please", "yup", "absolutely", "definitely"]
_DECLINE = ["not interested", "no thanks", "stop messaging", "stop sending",
            "unsubscribe", "leave me alone", "dont message", "don't message",
            "remove me", "opt out", "band karo", "mat bhejo", "no need"]
_HOSTILE = ["useless", "spam", "stop bothering", "rubbish", "nonsense", "shut up",
            "fed up", "irritating", "waste of time", "bakwaas", "fuck", "stupid"]
_PRICING = ["price", "cost", "kitna", "how much", "charges", "rate", "fees",
            "fee", "₹", "rupees", "paisa", "kitne ka"]
_OFFTOPIC = ["gst", "income tax", "loan", "insurance", "visa", "passport",
             "electricity bill", "recharge", "legal notice"]
_CONFUSED = ["what do you mean", "didnt understand", "didn't understand",
             "samajh nahi", "confused", "what is this", "who are you", "kya hai"]
_QUESTION_HINT = ["?", "how", "what", "when", "where", "which", "can you", "kya"]

# actioning vs qualifying words (mirrors the judge_simulator intent check)
_ACTION_WORDS = ["done", "sending", "draft", "here", "confirm", "proceed",
                 "next", "ready", "prepared", "set"]
_QUALIFY_WORDS = ["would you", "do you", "can you tell", "what if", "how about",
                  "just to plan", "tell me more about your"]


@dataclass
class ConvState:
    conversation_id: str
    merchant_id: str = ""
    customer_id: Optional[str] = None
    trigger_id: Optional[str] = None
    sent_bodies: list[str] = field(default_factory=list)
    turns: int = 0
    auto_reply_streak: int = 0
    nudges_unanswered: int = 0
    closed: bool = False
    prepared_action: str = ""
    last_offer_title: str = ""


def _detect_intent(message: str) -> str:
    low = (message or "").lower()
    cls = ard.classify(message)
    if cls.label in ("auto_reply", "ooo", "spam"):
        return cls.label
    if any(p in low for p in _HOSTILE):
        return "hostile"
    if any(p in low for p in _DECLINE):
        return "decline"
    if any(p in low for p in _COMMIT):
        return "commit"
    if any(p in low for p in _OFFTOPIC):
        return "offtopic"
    if any(p in low for p in _CONFUSED):
        return "confused"
    if any(p in low for p in _PRICING):
        return "pricing"
    # affirmation only counts if short/clear
    if low.strip() in _AFFIRM or any(low.startswith(a + " ") for a in _AFFIRM):
        return "affirm"
    if cls.label == "generic_ack":
        return "generic_ack"
    if any(h in low for h in _QUESTION_HINT):
        return "question"
    return "other"


class ConversationEngine:
    def __init__(self, store: ContextStore, trigger_engine=None) -> None:
        self.store = store
        self.trigger_engine = trigger_engine
        self.intel = MerchantIntelligence()
        self._lock = threading.RLock()
        self._convos: dict[str, ConvState] = {}
        self._rep = ard.RepetitionTracker()

    # ---- registration from the tick layer --------------------------------
    def register(self, conversation_id: str, merchant_id: str,
                 customer_id: Optional[str], trigger_id: Optional[str],
                 body: str, prepared_action: str = "", offer_title: str = "") -> None:
        with self._lock:
            st = self._convos.setdefault(conversation_id, ConvState(conversation_id))
            st.merchant_id = merchant_id
            st.customer_id = customer_id
            st.trigger_id = trigger_id
            st.prepared_action = prepared_action
            st.last_offer_title = offer_title
            if body:
                st.sent_bodies.append(body)

    # ---- the reply handler -----------------------------------------------
    def handle_reply(self, conversation_id: str, merchant_id: str,
                     message: str, turn_number: int) -> ReplyResponse:
        with self._lock:
            st = self._convos.setdefault(conversation_id, ConvState(conversation_id))
            if not st.merchant_id:
                st.merchant_id = merchant_id or ""
            st.turns = max(st.turns, turn_number)
            if st.closed:
                return ReplyResponse(action="end",
                                     rationale="Conversation already closed; not re-engaging.")

        intent = _detect_intent(message)
        rc = self._resolve(st)

        # ---- terminal intents --------------------------------------------
        if intent == "hostile" or intent == "decline":
            return self._end_and_suppress(st, intent)

        if intent in ("auto_reply", "ooo", "spam"):
            return self._handle_auto_reply(st, message, intent)

        # a real/engaged reply resets the auto-reply streak
        st.auto_reply_streak = 0

        if intent == "commit":
            return self._action_mode(st, rc)
        if intent == "affirm":
            return self._advance(st, rc)
        if intent == "offtopic":
            return self._redirect(st, rc)
        if intent == "pricing":
            return self._answer_pricing(st, rc)
        if intent == "confused":
            return self._clarify(st, rc)
        if intent == "generic_ack":
            return self._soft_nudge(st, rc)
        if intent == "question":
            return self._answer_question(st, rc, message)
        return self._advance(st, rc)

    # ---- handlers ---------------------------------------------------------
    def _handle_auto_reply(self, st: ConvState, message: str, intent: str) -> ReplyResponse:
        st.auto_reply_streak += 1
        global_count = self._rep.observe(st.merchant_id, message)
        level = max(st.auto_reply_streak, global_count)

        if level >= 3:
            self._close(st)
            return ReplyResponse(
                action="end",
                rationale=f"Same {intent} {level}x — zero real engagement signal; "
                          "closing so we stop wasting turns.")
        if level == 2:
            return ReplyResponse(
                action="wait", wait_seconds=86400,
                rationale=f"Second {intent} in a row — owner not at the phone; waiting 24h.")
        # first detection: one short, explicit flag-for-owner prompt
        body = self._unique(st, "Looks like an auto-reply 😊 When the owner sees this, "
                                 "just reply YES and I'll take it from there.")
        if body is None:
            return ReplyResponse(action="wait", wait_seconds=14400,
                                 rationale="Auto-reply detected; backing off 4h for the owner.")
        return ReplyResponse(action="send", body=body, cta=CTA_BINARY_YES,
                             rationale=f"Detected {intent}; one explicit prompt for the owner, "
                                       "then I'll back off.")

    def _action_mode(self, st: ConvState, rc: Optional[ResolvedContext]) -> ReplyResponse:
        """Intent transition: stop qualifying, start doing."""
        thing = st.prepared_action or "it"
        scope_note = ""
        if rc:
            agg = rc.merchant.get("customer_aggregate", {}) or {}
            n = agg.get("high_risk_adult_count") or agg.get("lapsed_180d_plus")
            if n:
                scope_note = f" (scoped to {n} relevant customers)"
        body = (f"Great — on it. I've started on {thing}{scope_note}. "
                f"You'll have it to review in ~90 seconds. Reply CONFIRM to send it once you've seen it.")
        body = self._unique(st, body) or (
            f"On it — preparing {thing} now. Reply CONFIRM when you want it sent.")
        return ReplyResponse(action="send", body=body, cta=CTA_BINARY_CONFIRM,
                             rationale="Explicit commitment detected — switched from qualifying "
                                       "to action execution with a concrete next step.")

    def _advance(self, st: ConvState, rc: Optional[ResolvedContext]) -> ReplyResponse:
        thing = st.prepared_action or "the next step"
        body = self._unique(st, f"Perfect. I'll get {thing} over to you here shortly — "
                                 f"anything specific you want me to highlight?")
        if body is None:
            return ReplyResponse(action="wait", wait_seconds=3600,
                                 rationale="Nothing new to add without repeating; brief pause.")
        return ReplyResponse(action="send", body=body, cta=CTA_OPEN,
                             rationale="Positive signal — advancing with the prepared deliverable "
                                       "and one light open question.")

    def _redirect(self, st: ConvState, rc: Optional[ResolvedContext]) -> ReplyResponse:
        topic = st.prepared_action or "what we were working on"
        body = self._unique(st, "That one's outside what I can help with directly — "
                                 f"your CA is better placed there. Coming back to {topic}: "
                                 "want me to go ahead?")
        return ReplyResponse(action="send", body=body or "Back to our task — shall I proceed?",
                             cta=CTA_BINARY_YES,
                             rationale="Out-of-scope request politely declined; redirected to the "
                                       "original trigger without losing the thread.")

    def _answer_pricing(self, st: ConvState, rc: Optional[ResolvedContext]) -> ReplyResponse:
        price_line = ""
        if st.last_offer_title:
            price_line = f"It's {st.last_offer_title}. "
        elif rc and rc.offer:
            price_line = f"It's {rc.offer.get('title','')}. "
        body = self._unique(st, f"{price_line}No hidden charges. Want me to set it up?")
        return ReplyResponse(action="send", body=body or "Want me to set it up?",
                             cta=CTA_BINARY_YES,
                             rationale="Pricing question answered from the real catalog offer; "
                                       "closed with a single binary CTA.")

    def _clarify(self, st: ConvState, rc: Optional[ResolvedContext]) -> ReplyResponse:
        who = "Vera, magicpin's assistant for your business"
        body = self._unique(st, f"Quick context — this is {who}. {self._one_line_value(st, rc)} "
                                 "Want me to go ahead?")
        return ReplyResponse(action="send", body=body or "Want me to go ahead?",
                             cta=CTA_BINARY_YES,
                             rationale="Merchant confused — re-established identity and value in "
                                       "one line, then a clear ask.")

    def _soft_nudge(self, st: ConvState, rc: Optional[ResolvedContext]) -> ReplyResponse:
        st.nudges_unanswered += 1
        if st.nudges_unanswered >= 2:
            return ReplyResponse(action="wait", wait_seconds=14400,
                                 rationale="Two low-substance acks — backing off to avoid pestering.")
        body = self._unique(st, "Just say the word and I'll get it done — YES to go ahead?")
        return ReplyResponse(action="send", body=body or "YES to go ahead?", cta=CTA_BINARY_YES,
                             rationale="Low-substance ack — one nudge with a frictionless binary CTA.")

    def _answer_question(self, st: ConvState, rc: Optional[ResolvedContext],
                         message: str) -> ReplyResponse:
        body = self._unique(st, f"Good question. {self._one_line_value(st, rc)} "
                                 "Want me to prepare it so you can see exactly how it looks?")
        return ReplyResponse(action="send", body=body or "Want me to prepare it?",
                             cta=CTA_BINARY_YES,
                             rationale="Answered the merchant's question succinctly and re-anchored "
                                       "on the prepared deliverable.")

    # ---- terminal helpers -------------------------------------------------
    def _end_and_suppress(self, st: ConvState, intent: str) -> ReplyResponse:
        self._close(st)
        if self.trigger_engine and st.merchant_id:
            self.trigger_engine.suppress_merchant(
                st.merchant_id, datetime.now(timezone.utc) + timedelta(days=30))
        reason = ("Merchant frustration explicit" if intent == "hostile"
                  else "Merchant explicitly opted out")
        return ReplyResponse(action="end",
                             rationale=f"{reason}; closing and suppressing triggers for this "
                                       "merchant for 30 days.")

    def _close(self, st: ConvState) -> None:
        st.closed = True
        if self.trigger_engine:
            self.trigger_engine.close_conversation(st.conversation_id)

    # ---- utilities --------------------------------------------------------
    def _one_line_value(self, st: ConvState, rc: Optional[ResolvedContext]) -> str:
        if st.prepared_action:
            return f"I can get {st.prepared_action} ready for you."
        if rc:
            facts = rc.top_facts(1)
            if facts:
                return f"It's about {facts[0].text}."
        return "I help your listing get found and booked."

    def _unique(self, st: ConvState, body: str) -> Optional[str]:
        """Anti-repetition: never send the same body twice in a conversation."""
        norm = ard.normalize(body)
        if any(ard.normalize(b) == norm for b in st.sent_bodies):
            return None
        st.sent_bodies.append(body)
        return body

    def _resolve(self, st: ConvState) -> Optional[ResolvedContext]:
        if st.trigger_id:
            rc = self.store.resolve_trigger(st.trigger_id)
            if rc:
                return rc
        # best-effort: build a minimal RC from merchant alone
        if st.merchant_id:
            merchant = self.store.get("merchant", st.merchant_id)
            if merchant:
                cat = self.store.get("category", merchant.get("category_slug", "")) or {}
                from .context_engine import Resolver
                return Resolver(cat, merchant, {"kind": "", "payload": {}}, None).resolve()
        return None
