"""Trigger Engine — prioritise, score urgency, score relevance, suppress.

At every `/v1/tick` the judge hands us a set of `available_triggers`. We do not
blindly fire on all of them — the brief explicitly rewards restraint and
penalises spam. This engine decides, for a tick, *which* triggers are worth a
send and in what order, and it owns the dedup/suppression bookkeeping so we
never re-send the same suppression_key or re-open a conversation a merchant
already closed.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .context_engine import ContextStore, ResolvedContext, dig


# Relative "freshness/why-now" weight per trigger kind. Event-driven, time-boxed
# triggers are stronger reasons to message *right now* than evergreen nudges.
KIND_RELEVANCE: dict[str, float] = {
    "supply_alert": 1.0,
    "regulation_change": 0.95,
    "recall_due": 0.92,
    "chronic_refill_due": 0.92,
    "perf_dip": 0.9,
    "renewal_due": 0.9,
    "ipl_match_today": 0.88,
    "competitor_opened": 0.85,
    "review_theme_emerged": 0.82,
    "perf_spike": 0.8,
    "research_digest": 0.78,
    "wedding_package_followup": 0.78,
    "trial_followup": 0.75,
    "milestone_reached": 0.72,
    "customer_lapsed_hard": 0.72,
    "winback_eligible": 0.7,
    "cde_opportunity": 0.68,
    "festival_upcoming": 0.6,
    "category_seasonal": 0.55,
    "seasonal_perf_dip": 0.6,
    "active_planning_intent": 0.95,
    "curious_ask_due": 0.5,
    "dormant_with_vera": 0.5,
    "gbp_unverified": 0.6,
}


@dataclass
class ScoredTrigger:
    trigger_id: str
    resolved: ResolvedContext
    priority: float
    urgency: int
    relevance: float
    suppression_key: str


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class TriggerEngine:
    def __init__(self, store: ContextStore) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._fired_keys: set[str] = set()        # suppression_keys already sent
        self._closed_conversations: set[str] = set()
        self._suppressed_merchants: dict[str, datetime] = {}  # hostile opt-outs

    # ---- suppression bookkeeping -----------------------------------------
    def mark_fired(self, suppression_key: str) -> None:
        if suppression_key:
            with self._lock:
                self._fired_keys.add(suppression_key)

    def close_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self._closed_conversations.add(conversation_id)

    def suppress_merchant(self, merchant_id: str, until: datetime) -> None:
        if merchant_id:
            with self._lock:
                self._suppressed_merchants[merchant_id] = until

    def is_conversation_closed(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._closed_conversations

    def _is_merchant_suppressed(self, merchant_id: str, now: datetime) -> bool:
        with self._lock:
            until = self._suppressed_merchants.get(merchant_id)
        return bool(until and until > now)

    # ---- scoring ----------------------------------------------------------
    def score_trigger(self, trigger_id: str, now: datetime) -> Optional[ScoredTrigger]:
        rc = self.store.resolve_trigger(trigger_id)
        if not rc:
            return None
        trig = rc.trigger
        suppression_key = trig.get("suppression_key", "")
        merchant_id = dig(rc.merchant, "merchant_id") or ""

        # hard filters --------------------------------------------------
        with self._lock:
            already = suppression_key in self._fired_keys
        if already:
            return None
        if self._is_merchant_suppressed(merchant_id, now):
            return None
        expires = _parse_iso(trig.get("expires_at"))
        if expires and expires < now:
            return None

        urgency = int(trig.get("urgency", 2) or 2)
        kind = trig.get("kind", "")
        relevance = KIND_RELEVANCE.get(kind, 0.65)

        # how many concrete facts could we anchor on? more facts => stronger send
        fact_strength = min(1.0, sum(f.weight for f in rc.top_facts(4)) / 8.0)

        # composite priority: urgency dominates, relevance + groundedness refine
        priority = (urgency / 5.0) * 0.55 + relevance * 0.30 + fact_strength * 0.15
        return ScoredTrigger(trigger_id, rc, priority, urgency, relevance, suppression_key)

    def select(self, available: list[str], now: datetime, max_actions: int = 6) -> list[ScoredTrigger]:
        """Rank available triggers and return the subset worth sending this tick.

        One send per merchant per tick (challenge rule), best trigger wins.
        """
        scored = [s for tid in available if (s := self.score_trigger(tid, now))]
        scored.sort(key=lambda s: s.priority, reverse=True)

        chosen: list[ScoredTrigger] = []
        seen_merchants: set[str] = set()
        for s in scored:
            mid = dig(s.resolved.merchant, "merchant_id") or ""
            if mid in seen_merchants:
                continue
            # restraint: skip very weak evergreen nudges when nothing time-boxed
            if s.priority < 0.45:
                continue
            chosen.append(s)
            seen_merchants.add(mid)
            if len(chosen) >= max_actions:
                break
        return chosen
