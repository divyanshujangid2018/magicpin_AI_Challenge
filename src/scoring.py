"""Internal judge model — self-critique + validation gate.

A heuristic re-implementation of the 5-dimension rubric the real judge uses
(specificity, category_fit, merchant_fit, decision_quality/trigger_relevance,
engagement_compulsion) plus the hard operational penalties (URLs, taboo vocab,
language mismatch, repetition, multi-CTA dilution).

Two uses:
  1. `validate()` — a gate the composer runs on every candidate message. A
     message that trips a hard penalty is rejected/repaired before it ships.
  2. `score()` — an estimate used to pick the better of two candidate drafts
     (deterministic vs LLM-polished) and to drive the self-critique loop.

This is intentionally strict — it mirrors a STRICT judge — so we surface our own
weaknesses before the real judge does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .context_engine import ResolvedContext

URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
NUM_RE = re.compile(r"\d")


@dataclass
class Critique:
    specificity: int = 5
    category_fit: int = 5
    merchant_fit: int = 5
    decision_quality: int = 5
    engagement: int = 5
    penalties: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return max(0, self.specificity + self.category_fit + self.merchant_fit +
                   self.decision_quality + self.engagement - self.penalties)


def validate(body: str, cta: str, rc: ResolvedContext) -> list[str]:
    """Return a list of hard issues. Empty list == ships cleanly."""
    issues: list[str] = []
    if not body or not body.strip():
        issues.append("empty_body")
        return issues
    if URL_RE.search(body):
        issues.append("contains_url")
    # taboo vocabulary for the category
    taboos = (rc.category.get("voice", {}) or {}).get("vocab_taboo", []) or \
             (rc.category.get("voice", {}) or {}).get("taboos", [])
    low = body.lower()
    for t in taboos:
        # match on the leading phrase before any parenthetical note
        token = str(t).split("(")[0].strip().lower()
        if token and token in low:
            issues.append(f"taboo:{token}")
    # internal jargon must never leak to the merchant/customer
    for jargon in ("suppression_key", "trigger_id", "conversation_id",
                   "merchant_id", "category_slug", "ctr_below_peer_median",
                   "stale_posts:", "perf_dip", "high_risk_adult_cohort"):
        if jargon in low:
            issues.append(f"jargon:{jargon}")
    # multiple competing CTAs dilute (YES/NO/MAYBE style)
    if low.count("reply ") >= 2:
        issues.append("multi_cta")
    return issues


def score(body: str, cta: str, rc: ResolvedContext, levers: list[str]) -> Critique:
    c = Critique()
    low = body.lower()
    voice = rc.category.get("voice", {}) or {}

    # ---- specificity: numbers, dates, citations -----------------------
    nums = len(NUM_RE.findall(body))
    has_citation = any(
        src and src.lower() in low
        for src in [f.source for f in rc.facts] if src
    )
    c.specificity = min(10, 3 + nums)               # numbers are the main driver
    if has_citation:
        c.specificity = min(10, c.specificity + 2)
    if nums == 0:
        c.specificity = 2

    # ---- category fit: vocab + tone, taboo avoidance ------------------
    allowed = [v.lower() for v in voice.get("vocab_allowed", [])]
    used_vocab = sum(1 for v in allowed if v in low)
    c.category_fit = 6 + min(3, used_vocab)
    if rc.category_slug == "dentists" and "dr." in low:
        c.category_fit = min(10, c.category_fit + 1)

    # ---- merchant fit: personalised to THIS merchant / customer -------
    mf = 5
    if rc.is_customer_facing and rc.customer:
        # customer-facing: judged on customer fit (name, language, real offer)
        cname = (rc.customer.get("identity", {}) or {}).get("name", "").split("(")[0].strip().lower()
        if cname and cname in low:
            mf += 3
        if rc.merchant_name and rc.merchant_name.lower()[:6] in low:
            mf += 1
        if rc.offer and (rc.offer.get("title", "")[:6].lower() in low):
            mf += 1
    else:
        if rc.owner and rc.owner.lower() in low:
            mf += 3
        if rc.locality and rc.locality.lower() in low:
            mf += 1
        if any(f.kind in ("benchmark", "derived") and f.text.lower()[:8] in low for f in rc.facts):
            mf += 1
    c.merchant_fit = min(10, mf)

    # ---- decision quality / trigger relevance -------------------------
    dq = 5
    if rc.facts and any(f.text.lower()[:6] in low for f in rc.top_facts(3)):
        dq += 3
    if rc.digest_item and (rc.digest_item.get("source", "").lower() in low):
        dq += 1
    c.decision_quality = min(10, dq)

    # ---- engagement: levers + clean single CTA ------------------------
    eng = 4 + min(4, len(levers))
    if cta in ("binary_yes_no", "binary_confirm_cancel"):
        eng += 1
    if "?" in body:
        eng += 1
    c.engagement = min(10, eng)

    # ---- penalties ----------------------------------------------------
    issues = validate(body, cta, rc)
    for i in issues:
        if i.startswith("contains_url"):
            c.penalties += 3
        elif i.startswith("taboo") or i.startswith("jargon"):
            c.penalties += 1
        elif i == "multi_cta":
            c.penalties += 2
    c.issues = issues
    return c
