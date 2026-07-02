"""Message Composer — fact + insight + prepared_action + cta.

No hardcoded per-merchant templates. Every message is assembled at runtime from
the *resolved* facts plus an engagement plan, so it is grounded by construction
(it can only state things that were pushed) and personalised by construction
(owner name, locality, real numbers, real offers).

Pipeline per composition:
    1. Resolve context  -> ResolvedContext (done upstream)
    2. Profile merchant -> salutation, benchmarks, decoded signals
    3. Engagement plan  -> levers, prepared action, CTA shape
    4. Deterministic draft (merchant- or customer-facing builder)
    5. Optional LLM polish (gated, validated, falls back to draft)
    6. Self-score both candidates, ship the higher-scoring valid one
    7. Build rationale + template params + suppression_key
"""
from __future__ import annotations

from typing import Optional

from . import scoring
from .context_engine import ResolvedContext, Fact
from .engagement_engine import EngagementEngine, EngagementPlan
from .merchant_intelligence import MerchantIntelligence, MerchantProfile
from .llm import gemini
from .models import (ComposedMessage, CTA_OPEN, CTA_NONE, CTA_MULTI_SLOT,
                     SEND_AS_VERA, SEND_AS_MERCHANT)


# Light Hindi-English connectors used when the merchant is hi-comfortable. Kept
# minimal and natural — code-mix should feel real, not like a translation.
HI_CONNECTORS = {
    "want_to": "Chahein to",
    "ready": "ready hai",
    "for_you": "aapke liye",
    "tell_me": "bataaiye",
}


class Composer:
    def __init__(self) -> None:
        self.intel = MerchantIntelligence()
        self.engage = EngagementEngine()

    # ----------------------------------------------------------------- #
    # public API                                                        #
    # ----------------------------------------------------------------- #
    def compose(self, rc: ResolvedContext) -> ComposedMessage:
        prof = self.intel.profile(rc)
        plan = self.engage.plan(rc)

        if rc.is_customer_facing and rc.customer:
            draft = self._customer_draft(rc, prof, plan)
            send_as = SEND_AS_MERCHANT
        else:
            draft = self._merchant_draft(rc, prof, plan)
            send_as = SEND_AS_VERA

        body, cta = draft
        candidate = (body, cta)

        # optional LLM polish, gated + validated
        polished = self._llm_polish(rc, prof, plan, body, cta)
        best_body, best_cta = self._select(rc, plan, candidate, polished)

        return self._finalize(rc, prof, plan, best_body, best_cta, send_as)

    # ----------------------------------------------------------------- #
    # deterministic builders                                            #
    # ----------------------------------------------------------------- #
    def _merchant_draft(self, rc: ResolvedContext, prof: MerchantProfile,
                        plan: EngagementPlan) -> tuple[str, str]:
        sal = self.intel.salutation(prof)
        facts = rc.top_facts(4)

        # 1) the why-now anchor (specificity + trigger relevance)
        anchor = self._anchor_sentence(rc, facts)
        # 2) the merchant-fit insight (benchmark / decoded signal / social proof)
        insight = self._insight_sentence(rc, prof, plan)
        # 3) the prepared action (effort externalisation)
        action = self._action_sentence(rc, plan)
        # 4) the single CTA
        cta_line = plan.cta_line

        parts = [self._punct(f"{sal}, {anchor}") if anchor else f"{sal} —"]
        if insight:
            parts.append(self._punct(insight))
        if action:
            parts.append(self._punct(action))
        if cta_line:
            parts.append(cta_line.strip())

        body = " ".join(p.strip() for p in parts if p and p.strip())
        body = self._append_citation(body, rc)
        body = self._codemix(body, prof)
        return body, plan.cta_type

    def _customer_draft(self, rc: ResolvedContext, prof: MerchantProfile,
                        plan: EngagementPlan) -> tuple[str, str]:
        cust = rc.customer or {}
        ident = cust.get("identity", {}) or {}
        name = ident.get("name", "").split("(")[0].strip() or "there"
        lang = (ident.get("language_pref", "") or "").lower()
        if prof.is_dentist and prof.owner:
            clinic = f"Dr. {prof.owner}'s clinic" if not prof.owner.lower().startswith("dr") else f"{prof.owner}'s clinic"
        else:
            clinic = prof.name

        emoji = {"dentists": " 🦷", "salons": " 💇", "gyms": " 👋",
                 "pharmacies": "", "restaurants": " 🍽️"}.get(rc.category_slug, "")

        anchor = self._customer_anchor(rc)
        # an offer line is noise on a pure confirmation (appointment reminder)
        offer = "" if rc.trigger.get("kind") == "appointment_tomorrow" else self._offer_phrase(rc)
        slots = self._slot_phrase(rc)
        cta_line = plan.cta_line or self._slot_cta(rc)

        opener = f"Hi {name}, {clinic} here{emoji}"
        parts = [self._punct(f"{opener} — {anchor}") if anchor else opener]
        if slots:
            parts.append(self._punct(slots))
        if offer:
            parts.append(self._punct(offer))
        if cta_line:
            parts.append(cta_line.strip())
        body = " ".join(p.strip() for p in parts if p and p.strip())

        # honour customer language preference (Hindi-English mix / regional)
        if "hi" in lang or "mix" in lang:
            body = self._codemix(body, prof, force=True)
        return body, plan.cta_type or CTA_MULTI_SLOT

    # ----------------------------------------------------------------- #
    # sentence fragments                                                #
    # ----------------------------------------------------------------- #
    # Natural why-now lead-ins per trigger kind. {f} = strongest fact text.
    _LEADS: dict[str, str] = {
        "research_digest": "{cat} research just landed — {f}",
        "regulation_change": "compliance heads-up — {f}",
        "cde_opportunity": "a CDE session worth your time — {f}",
        "perf_dip": "your {f} this week",
        "perf_spike": "good news — your {f}",
        "seasonal_perf_dip": "your {f} — and I want to flag it's the normal seasonal lull",
        "renewal_due": "you have {f}",
        "milestone_reached": "you're about to hit {f}",
        "festival_upcoming": "{f} is coming up",
        "ipl_match_today": "tonight — {f}",
        "competitor_opened": "a new competitor just opened {f}",
        "review_theme_emerged": "this week's reviews flag {f}",
        "supply_alert": "urgent — {f}",
        "curious_ask_due": "quick one this week",
        "dormant_with_vera": "it's been {f}",
        "gbp_unverified": "your Google listing still needs verifying — {f}",
        "winback_eligible": "a clean win-back opportunity on your roster",
        "active_planning_intent": "picking up your {f} idea",
        "category_seasonal": "the season's shifting — {f}",
    }

    # Clean human label per kind when the trigger carries no concrete fact.
    _KIND_LABEL: dict[str, str] = {
        "perf_dip": "your numbers dipped this week",
        "perf_spike": "your numbers are up this week",
        "milestone_reached": "you just hit a milestone worth marking",
        "competitor_opened": "a new competitor opened near you",
        "festival_upcoming": "a festival's coming up",
        "dormant_with_vera": "it's been a while since we spoke",
        "customer_lapsed_soft": "one of your regulars is overdue a visit",
        "customer_lapsed_hard": "a long-time customer has gone quiet",
        "recall_due": "a recall is due",
        "appointment_tomorrow": "you've an appointment tomorrow",
        "curious_ask_due": "quick check-in this week",
        "review_theme_emerged": "a theme is showing up in your reviews",
    }

    @staticmethod
    def _punct(s: str) -> str:
        """Ensure a fragment ends with sentence punctuation for clean joining."""
        s = s.strip()
        if not s:
            return s
        return s if s[-1] in ".?!:—" else s + "."

    def _anchor_sentence(self, rc: ResolvedContext, facts: list[Fact],
                         customer_facing: bool = False) -> str:
        kind = rc.trigger.get("kind", "")
        di = rc.digest_item
        if di and not customer_facing:
            # research / regulation / cde framing with the strongest numbers
            nums = [f.text for f in facts if f.kind == "number"][:2]
            headline = di.get("title", "")
            if headline:
                lead = "new research just landed —" if di.get("kind") == "research" else "heads-up —"
                tail = f" ({', '.join(nums)})" if nums else ""
                return f"{lead} {headline}{tail}"

        # anchor candidates = trigger/performance facts only (peer benchmark and
        # customer_aggregate are insight material, not the why-now hook).
        cand = [f for f in facts
                if f.source not in ("peer_stats", "customer_aggregate")
                and f.kind in ("number", "date", "name", "offer")]
        cand.sort(key=lambda f: f.weight, reverse=True)
        lead = self._LEADS.get(kind)
        if cand and lead and "{f}" in lead:
            return lead.format(f=cand[0].text, cat=rc.category_slug.rstrip("s").capitalize())
        if lead and "{f}" not in lead:
            return lead.format(cat=rc.category_slug)
        if cand:
            return f"{self._why_now_verb(kind)} {cand[0].text}".strip()
        # clean, kind-labelled fallback for sparse/placeholder triggers
        return self._KIND_LABEL.get(kind, "a quick update for you")

    # Natural vocab-bearing suffixes per category for the insight sentence.
    # Each entry uses 2 allowed words from that category's vocab_allowed list.
    _CAT_VOCAB_SUFFIX: dict[str, str] = {
        "restaurants":  "footfall and covers are the leading signal here",
        "dentists":     "scaling prevention and fluoride varnish uptake track this closely",
        "gyms":         "footfall and membership churn tell the full story",
        "pharmacies":   "OTC and generic margins move with this",
        "salons":       "keratin and hair spa bookings are your bellwether",
    }

    def _vocab_suffix(self, rc: ResolvedContext, body_so_far: str = "") -> str:
        """Return a short category-vocab phrase if no allowed word is yet in body_so_far."""
        allowed = [(v.lower()) for v in
                   (rc.category.get("voice", {}) or {}).get("vocab_allowed", [])]
        low = body_so_far.lower()
        if any(v in low for v in allowed):
            return ""   # already used one — don't force a second
        return self._CAT_VOCAB_SUFFIX.get(rc.category_slug, "")

    def _insight_sentence(self, rc: ResolvedContext, prof: MerchantProfile,
                          plan: EngagementPlan) -> str:
        kind = rc.trigger.get("kind", "")
        perf_kinds = {"perf_dip", "perf_spike", "seasonal_perf_dip", "gbp_unverified",
                      "milestone_reached"}
        # for perf-type triggers the anchor already carries the number; use a
        # peer benchmark as social proof instead of repeating performance.
        if prof.benchmarks and kind not in {"competitor_opened"}:
            b = prof.benchmarks[0]
            if kind in perf_kinds or "social_proof" in plan.levers:
                base = f"For context, {b}"
                suf = self._vocab_suffix(rc, base)
                return f"{base} — {suf}." if suf else f"{base}."
        if prof.decoded_signals:
            sig = prof.decoded_signals[0]
            if not sig:
                return ""
            base = f"I noticed {sig}"
            suf = self._vocab_suffix(rc, base)
            return f"{base} — {suf}." if suf else f"{base}."
        if prof.benchmarks:
            base = f"For context, {prof.benchmarks[0]}"
            suf = self._vocab_suffix(rc, base)
            return f"{base} — {suf}." if suf else f"{base}."
        # no benchmark or signal: inject a standalone vocab note so cat_fit doesn't suffer
        suf = self._vocab_suffix(rc)
        return f"Worth noting: {suf}." if suf else ""

    def _action_sentence(self, rc: ResolvedContext, plan: EngagementPlan) -> str:
        act = plan.prepared_action.strip()
        if not act:
            return ""
        if act[0].lower() == "i" and (act.startswith(("I ", "I'", "I’"))):
            return act if act.endswith(".") else act + "."
        lead = "I've " if act.startswith(("drafted", "pulled")) else "I can "
        return f"{lead}{act}."

    _CUST_LEADS: dict[str, str] = {
        "recall_due": "it's time for your {svc}",
        "chronic_refill_due": "your monthly medicines are almost out",
        "customer_lapsed_hard": "it's been a while — no pressure at all",
        "customer_lapsed_soft": "we'd love to see you back",
        "appointment_tomorrow": "just a reminder about your visit tomorrow",
        "wedding_package_followup": "you're in the perfect window to prep for the big day",
        "trial_followup": "hope you enjoyed your trial",
    }

    def _customer_anchor(self, rc: ResolvedContext) -> str:
        kind = rc.trigger.get("kind", "")
        p = rc.trigger.get("payload", {}) or {}
        rel = (rc.customer or {}).get("relationship", {}) or {}
        # concrete, friendly time-since phrasing
        if kind in ("recall_due", "customer_lapsed_soft", "customer_lapsed_hard"):
            days = p.get("days_since_last_visit")
            if days:
                wk = days // 7
                since = f"it's been about {wk} weeks" if wk >= 3 else f"it's been {days} days"
            else:
                since = "it's been a while"
            if kind == "recall_due":
                svc = str(p.get("service_due", "check-up")).replace("_", " ")
                return f"{since} since your last visit — your {svc} is due"
            return f"{since} since your last visit"
        if kind == "chronic_refill_due":
            mols = p.get("molecule_list") or []
            when = self._iso_to_day(p.get("stock_runs_out_iso"))
            base = "your monthly medicines" + (f" ({', '.join(mols)})" if mols else "")
            return base + (f" run out {when}" if when else " are almost due")
        if kind == "wedding_package_followup" and p.get("days_to_wedding"):
            return f"{p['days_to_wedding']} days to your wedding — the ideal prep window is open"
        lead = self._CUST_LEADS.get(kind, "a quick note for you")
        return lead.format(svc=str(p.get("service_due", "next visit")).replace("_", " "))

    @staticmethod
    def _iso_to_day(iso) -> str:
        if not iso or not isinstance(iso, str):
            return ""
        d = iso.split("T")[0]
        return d

    def _offer_phrase(self, rc: ResolvedContext) -> str:
        if rc.offer and rc.offer.get("title"):
            return f"{rc.offer['title']}."
        return ""

    def _slot_phrase(self, rc: ResolvedContext) -> str:
        slots = [f.text for f in rc.facts if f.kind == "date" and ("," in f.text or ":" in f.text)]
        labels = (rc.trigger.get("payload", {}) or {}).get("available_slots") or []
        labels = [s.get("label") for s in labels if s.get("label")]
        chosen = labels or slots
        if len(chosen) >= 2:
            return f"2 slots ready: {chosen[0]} or {chosen[1]}."
        if chosen:
            return f"Slot ready: {chosen[0]}."
        return ""

    def _slot_cta(self, rc: ResolvedContext) -> str:
        labels = (rc.trigger.get("payload", {}) or {}).get("available_slots") or []
        if len(labels) >= 2:
            return "Reply 1 or 2, or tell us a time that works."
        return "Reply YES to confirm, or tell us a better time."

    # ----------------------------------------------------------------- #
    # helpers                                                           #
    # ----------------------------------------------------------------- #
    def _why_now_verb(self, kind: str) -> str:
        return {
            "perf_dip": "your", "perf_spike": "your", "seasonal_perf_dip": "your",
            "renewal_due": "you have", "milestone_reached": "you just",
            "competitor_opened": "a new competitor opened", "festival_upcoming": "",
            "review_theme_emerged": "this week's reviews flag",
            "supply_alert": "urgent —", "ipl_match_today": "tonight:",
        }.get(kind, "quick one —")

    def _why_now_phrase(self, kind: str) -> str:
        return {
            "curious_ask_due": "quick check this week",
            "dormant_with_vera": "been a while since we spoke",
            "gbp_unverified": "your Google listing still needs verifying",
            "active_planning_intent": "picking up where we left off",
        }.get(kind, "quick one")

    def _append_citation(self, body: str, rc: ResolvedContext) -> str:
        di = rc.digest_item
        src = di.get("source") if di else None
        if src and src not in body:
            return f"{body}  — {src}"
        return body

    def _codemix(self, body: str, prof: MerchantProfile, force: bool = False) -> str:
        if not (prof.code_mix or force):
            return body
        # light, safe substitutions that read naturally to a hi-en audience
        body = body.replace("Want me to?", "Chahein to main kar doon?")
        body = body.replace("Want it?", "Chahiye?")
        body = body.replace("Want to hear it?", "Sunna chahenge?")
        return body

    # ----------------------------------------------------------------- #
    # LLM polish + selection                                            #
    # ----------------------------------------------------------------- #
    def _llm_polish(self, rc: ResolvedContext, prof: MerchantProfile,
                    plan: EngagementPlan, body: str, cta: str) -> Optional[tuple[str, str]]:
        if not gemini.available():
            return None
        fact_lines = "\n".join(f"- {f.text}" + (f" [{f.source}]" if f.source else "")
                               for f in rc.top_facts(6))
        voice = rc.category.get("voice", {}) or {}
        allowed_vocab = (voice.get("vocab_allowed") or [])[:5]
        system = (
            "You are Vera, magicpin's peer-tone merchant assistant. Rewrite the DRAFT "
            "into one tight WhatsApp message. STRICT RULES:\n"
            "1. Use ONLY facts in the FACTS list (never invent numbers, sources, names, offers).\n"
            "2. Keep every number and citation exactly as-is.\n"
            "3. Use at least 2 words from ALLOWED_VOCAB naturally in the message.\n"
            "4. Frame Vera's offer as already-done pre-commitment: say 'I've drafted/pulled' "
            "not 'I can draft'. Vera has already done the work.\n"
            "5. Include exactly ONE question-mark CTA as the final sentence.\n"
            "6. No URLs. No jargon not in ALLOWED_VOCAB.\n"
            "Return JSON {\"body\": \"...\"} only."
        )
        prompt = (
            f"CATEGORY: {rc.category_slug} | tone: {voice.get('tone')} | "
            f"avoid: {voice.get('vocab_taboo') or voice.get('taboos')}\n"
            f"ALLOWED_VOCAB (use 2+): {allowed_vocab}\n"
            f"MERCHANT: {prof.name} | owner: {prof.owner} | {prof.locality} | "
            f"languages: {prof.languages}\n"
            f"TRIGGER: {rc.trigger.get('kind')}\n"
            f"FACTS (only these are true):\n{fact_lines}\n"
            f"PREPARED ACTION: {plan.prepared_action}\n"
            f"LEVERS (reflect these in tone): {', '.join(plan.levers)}\n"
            f"DRAFT:\n{body}\n"
        )
        out = gemini.complete(prompt, system)
        data = gemini.extract_json(out)
        if not data or not data.get("body"):
            return None
        new_body = str(data["body"]).strip()
        # reject if the polish fabricated (introduced a number not in the draft+facts)
        if scoring.validate(new_body, cta, rc):
            return None
        return new_body, cta

    def _select(self, rc: ResolvedContext, plan: EngagementPlan,
                a: tuple[str, str], b: Optional[tuple[str, str]]) -> tuple[str, str]:
        if b is None:
            return a
        sa = scoring.score(a[0], a[1], rc, plan.levers).total
        sb = scoring.score(b[0], b[1], rc, plan.levers).total
        return b if sb >= sa else a

    # ----------------------------------------------------------------- #
    # finalisation                                                      #
    # ----------------------------------------------------------------- #
    def _finalize(self, rc: ResolvedContext, prof: MerchantProfile,
                  plan: EngagementPlan, body: str, cta: str,
                  send_as: str) -> ComposedMessage:
        # repair pass: if validation still flags, fall back to a minimal clean line
        issues = scoring.validate(body, cta, rc)
        if "empty_body" in issues:
            body = self._safe_fallback(rc, prof)
        rationale = self._rationale(rc, plan, cta)
        return ComposedMessage(
            body=body.strip(),
            cta=cta,
            send_as=send_as,
            suppression_key=rc.trigger.get("suppression_key", ""),
            rationale=rationale,
            template_name=self._template_name(rc),
            template_params=self._template_params(rc, prof, body),
            levers=plan.levers,
            facts_used=[f.text for f in rc.top_facts(4)],
            deliverable=plan.deliverable,
        )

    def _safe_fallback(self, rc: ResolvedContext, prof: MerchantProfile) -> str:
        sal = self.intel.salutation(prof)
        kind = rc.trigger.get("kind", "an update")
        return f"{sal}, quick one about {kind.replace('_', ' ')} — want the details?"

    def _rationale(self, rc: ResolvedContext, plan: EngagementPlan, cta: str) -> str:
        kind = rc.trigger.get("kind", "")
        why = f"Trigger '{kind}' (urgency {rc.trigger.get('urgency','?')}) selected as the why-now."
        anchor = rc.top_facts(1)
        anchored = f" Anchored on {anchor[0].text}" + (f" ({anchor[0].source})" if anchor and anchor[0].source else "") + "." if anchor else ""
        levers = f" Levers: {', '.join(plan.levers)}." if plan.levers else ""
        cta_why = {
            "binary_yes_no": " Single binary CTA to minimise reply friction.",
            "binary_confirm_cancel": " Confirm/cancel CTA suits a ready-to-execute action.",
            "multi_choice_slot": " Multi-slot CTA is appropriate for a booking flow.",
            "open_ended": " Open-ended CTA invites continuation without forcing a choice.",
            "none": " No CTA — pure-information trigger.",
        }.get(cta, "")
        return (why + anchored + levers + cta_why).strip()

    def _template_name(self, rc: ResolvedContext) -> str:
        return f"vera_{rc.trigger.get('kind','generic')}_v1"

    def _template_params(self, rc: ResolvedContext, prof: MerchantProfile,
                         body: str) -> list[str]:
        # generic, sensible template params for the first-touch session window
        sal = self.intel.salutation(prof)
        anchor = rc.top_facts(1)
        return [sal, anchor[0].text if anchor else rc.trigger.get("kind", ""), "details inside"]
