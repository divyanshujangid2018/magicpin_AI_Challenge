"""Auto-Reply Detector — stop wasting turns.

40-70% of "merchant replies" in production are WhatsApp Business canned
auto-replies. The brief makes faster detection + routing an explicit win. We
detect four families and a repetition signal:

  * auto_reply   — "thank you for contacting", "team will respond", bot-disclosure
  * ooo          — out-of-office / temporarily closed / on leave
  * generic_ack  — "ok", "noted", "thanks" with no substance
  * spam         — promo/forwarded junk
  * repetition   — same text seen 3+ times (hint from the brief)

Detection is content-based AND repetition-based so it works whether the harness
reuses a conversation_id (per the API examples) or uses a fresh one per turn
(as the bundled judge_simulator does).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_AUTO_PHRASES = [
    "thank you for contacting", "thanks for contacting",
    "our team will", "we will respond", "will get back to you",
    "we will contact you", "team tak pahuncha", "jaankari ke liye",
    "automated assistant", "automated message", "auto-reply", "auto reply",
    "this is an automated", "shukriya", "dhanyavaad", "team will reach",
    "received your message", "message received", "away from",
]
_OOO_PHRASES = [
    "out of office", "out-of-office", "currently closed", "temporarily closed",
    "on leave", "on holiday", "away on", "back on", "closed for", "ooo",
]
_SPAM_PHRASES = [
    "congratulations you", "click here to claim", "you have won",
    "limited offer just for you", "forwarded as received",
]
_GENERIC_ACKS = {"ok", "okay", "k", "noted", "thanks", "thank you", "thx",
                 "tks", "hmm", "hm", "👍", "ok ok", "fine", "sure"}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (text or "").lower())).strip()


@dataclass
class Classification:
    label: str          # auto_reply | ooo | generic_ack | spam | real
    confidence: float
    reason: str


def classify(message: str) -> Classification:
    norm = normalize(message)
    low = (message or "").lower()
    if not norm:
        return Classification("generic_ack", 0.6, "empty/blank reply")

    if any(p in low for p in _OOO_PHRASES):
        return Classification("ooo", 0.9, "out-of-office phrasing")
    if any(p in low for p in _AUTO_PHRASES):
        return Classification("auto_reply", 0.92, "canned auto-reply phrasing")
    if any(p in low for p in _SPAM_PHRASES):
        return Classification("spam", 0.85, "promotional/forwarded spam")
    if norm in _GENERIC_ACKS or (len(norm.split()) <= 2 and norm in _GENERIC_ACKS):
        return Classification("generic_ack", 0.7, "low-substance acknowledgement")
    return Classification("real", 0.8, "substantive merchant reply")


class RepetitionTracker:
    """Counts identical messages per merchant so we escalate even across conv ids."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}

    def observe(self, merchant_id: str, message: str) -> int:
        key = (merchant_id or "_", normalize(message))
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]
