"""Context Engine — store, normalise, resolve, rank.

Responsibilities
----------------
1. **Store**  — idempotent, versioned `(scope, context_id) -> payload`.
2. **Normalise** — tolerate the schema drift between the dataset seeds, the
   testing-brief examples, and post-submission injections (different key names
   for the same fact, e.g. `taboos` vs `vocab_taboo`).
3. **Resolve / hydrate** — the heart of the system. A trigger payload usually
   carries *references* (`top_item_id`, `metric`, `service_due`) rather than the
   facts themselves. The judge rewards messages that surface concrete, verifiable
   facts and penalises fabrication. So before composing we dereference every
   reference against the category digest / trend signals / merchant performance /
   customer relationship and build a single `ResolvedContext` whose `facts` list
   contains ONLY ground-truth values pulled from the pushed contexts.
4. **Rank** — order facts by verifiability so the composer leads with the
   strongest specificity anchor.

Nothing here calls an LLM. Everything is deterministic and side-effect free
except the in-memory store.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Defensive accessors — payloads are deliberately loose dicts                  #
# --------------------------------------------------------------------------- #
def dig(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def first_present(obj: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(obj, dict) and obj.get(k) not in (None, "", [], {}):
            return obj[k]
    return default


@dataclass
class Fact:
    """A single verifiable, ground-truth fact pulled from a pushed context."""
    text: str                 # human-readable, e.g. "38% lower caries recurrence"
    kind: str                 # number | date | citation | name | offer | benchmark | derived
    weight: float = 1.0       # specificity weight used for ranking
    source: str = ""          # provenance, e.g. "JIDA Oct 2026, p.14"


@dataclass
class ResolvedContext:
    category: dict = field(default_factory=dict)
    merchant: dict = field(default_factory=dict)
    trigger: dict = field(default_factory=dict)
    customer: Optional[dict] = None
    facts: list[Fact] = field(default_factory=list)
    digest_item: Optional[dict] = None   # dereferenced top_item, if any
    offer: Optional[dict] = None         # the best offer to anchor on, if any

    # ---- convenience views the composer/engagement layer read ----
    @property
    def owner(self) -> str:
        return dig(self.merchant, "identity", "owner_first_name", default="") or ""

    @property
    def merchant_name(self) -> str:
        return dig(self.merchant, "identity", "name", default="") or ""

    @property
    def locality(self) -> str:
        return dig(self.merchant, "identity", "locality", default="") or ""

    @property
    def city(self) -> str:
        return dig(self.merchant, "identity", "city", default="") or ""

    @property
    def category_slug(self) -> str:
        return self.category.get("slug") or dig(self.merchant, "category_slug", default="") or ""

    @property
    def languages(self) -> list[str]:
        return dig(self.merchant, "identity", "languages", default=["en"]) or ["en"]

    @property
    def is_customer_facing(self) -> bool:
        return self.customer is not None or dig(self.trigger, "scope") == "customer"

    def top_facts(self, n: int = 4) -> list[Fact]:
        return sorted(self.facts, key=lambda f: f.weight, reverse=True)[:n]


class ContextStore:
    """Idempotent, versioned in-memory store. Thread-safe for the FastAPI server."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (scope, context_id) -> {"version": int, "payload": dict}
        self._store: dict[tuple[str, str], dict] = {}

    # ---- writes -----------------------------------------------------------
    def put(self, scope: str, context_id: str, version: int, payload: dict) -> tuple[bool, Optional[int]]:
        """Returns (accepted, current_version_if_rejected)."""
        with self._lock:
            key = (scope, context_id)
            cur = self._store.get(key)
            if cur and cur["version"] >= version:
                return False, cur["version"]
            self._store[key] = {"version": int(version), "payload": payload or {}}
            return True, None

    # ---- reads ------------------------------------------------------------
    def get(self, scope: str, context_id: str) -> Optional[dict]:
        with self._lock:
            rec = self._store.get((scope, context_id))
            return rec["payload"] if rec else None

    def version(self, scope: str, context_id: str) -> Optional[int]:
        with self._lock:
            rec = self._store.get((scope, context_id))
            return rec["version"] if rec else None

    def counts(self) -> dict[str, int]:
        counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        with self._lock:
            for (scope, _) in self._store:
                counts[scope] = counts.get(scope, 0) + 1
        return counts

    def all_of(self, scope: str) -> dict[str, dict]:
        with self._lock:
            return {cid: rec["payload"] for (s, cid), rec in self._store.items() if s == scope}

    # ---- resolution -------------------------------------------------------
    def resolve_trigger(self, trigger_id: str) -> Optional[ResolvedContext]:
        trig = self.get("trigger", trigger_id)
        if not trig:
            return None
        merchant_id = first_present(trig, "merchant_id") or dig(trig, "payload", "merchant_id")
        customer_id = first_present(trig, "customer_id") or dig(trig, "payload", "customer_id")
        merchant = self.get("merchant", merchant_id) if merchant_id else None
        if not merchant:
            return None
        cat_slug = merchant.get("category_slug") or dig(trig, "payload", "category")
        category = self.get("category", cat_slug) if cat_slug else {}
        customer = self.get("customer", customer_id) if customer_id else None
        return Resolver(category or {}, merchant, trig, customer).resolve()


# --------------------------------------------------------------------------- #
# Resolver — turns a (category, merchant, trigger, customer) tuple into facts  #
# --------------------------------------------------------------------------- #
class Resolver:
    def __init__(self, category: dict, merchant: dict, trigger: dict, customer: Optional[dict]):
        self.category = category
        self.merchant = merchant
        self.trigger = trigger
        self.customer = customer

    # -- digest dereferencing ------------------------------------------------
    def _digest_by_id(self, item_id: str) -> Optional[dict]:
        for item in self.category.get("digest", []) or []:
            if item.get("id") == item_id:
                return item
        return None

    def _active_offers(self) -> list[dict]:
        return [o for o in self.merchant.get("offers", []) or [] if o.get("status") == "active"]

    def _catalog_offer_for(self, *keywords: str) -> Optional[dict]:
        """Find a category catalog offer whose title matches any keyword."""
        kws = [k.lower() for k in keywords if k]
        for o in self.category.get("offer_catalog", []) or []:
            title = (o.get("title") or "").lower()
            if any(k in title for k in kws):
                return o
        return None

    def resolve(self) -> ResolvedContext:
        rc = ResolvedContext(
            category=self.category, merchant=self.merchant,
            trigger=self.trigger, customer=self.customer,
        )
        kind = self.trigger.get("kind", "")
        payload = self.trigger.get("payload", {}) or {}
        facts: list[Fact] = []

        # ---- dereference a digest item if the trigger references one -------
        top_id = payload.get("top_item_id") or payload.get("digest_item_id")
        if top_id:
            item = self._digest_by_id(top_id)
            if item:
                rc.digest_item = item
                facts.extend(self._facts_from_digest(item))

        # ---- kind-specific fact extraction ---------------------------------
        extractor = getattr(self, f"_facts_{kind}", None)
        if extractor:
            facts.extend(extractor(payload))

        # ---- generic payload miner (handles rich payloads with no dedicated
        #      extractor, and unseen post-submission trigger kinds) ----------
        facts.extend(self._generic_payload_facts(payload))

        # ---- universal merchant/peer facts (merchant fit + social proof) ---
        facts.extend(self._merchant_facts())

        # ---- pick an offer to anchor a prepared action --------------------
        rc.offer = self._pick_offer(kind, payload)

        # de-dup by text, keep highest weight
        dedup: dict[str, Fact] = {}
        for f in facts:
            if f.text and (f.text not in dedup or f.weight > dedup[f.text].weight):
                dedup[f.text] = f
        rc.facts = list(dedup.values())
        return rc

    # ---- fact builders -----------------------------------------------------
    def _facts_from_digest(self, item: dict) -> list[Fact]:
        out: list[Fact] = []
        src = item.get("source", "")
        if item.get("trial_n"):
            out.append(Fact(f"{item['trial_n']:,}-patient trial", "number", 3.0, src))
        # pull a percentage out of the title/summary if present
        for blob in (item.get("title", ""), item.get("summary", "")):
            for token in blob.replace("—", " ").split():
                if token.strip("().,").endswith("%"):
                    out.append(Fact(token.strip("().,"), "number", 2.5, src))
                    break
        if item.get("title"):
            out.append(Fact(item["title"], "headline", 2.0, src))
        if item.get("date"):
            out.append(Fact(str(item["date"]), "date", 2.0, src))
        if item.get("credits"):
            out.append(Fact(f"{item['credits']} CDE credits", "number", 1.5, src))
        if src:
            out.append(Fact(src, "citation", 2.8, src))
        if item.get("actionable"):
            out.append(Fact(item["actionable"], "derived", 1.2, src))
        return out

    def _merchant_facts(self) -> list[Fact]:
        out: list[Fact] = []
        perf = self.merchant.get("performance", {}) or {}
        peer = self.category.get("peer_stats", {}) or {}
        ctr = perf.get("ctr")
        peer_ctr = peer.get("avg_ctr")
        if ctr is not None and peer_ctr:
            cmp = "below" if ctr < peer_ctr else "above"
            out.append(Fact(
                f"your CTR {ctr*100:.1f}% vs peer median {peer_ctr*100:.1f}%",
                "benchmark", 2.2, "peer_stats"))
            _ = cmp
        agg = self.merchant.get("customer_aggregate", {}) or {}
        if agg.get("high_risk_adult_count"):
            out.append(Fact(f"{agg['high_risk_adult_count']} high-risk adult patients",
                            "derived", 1.8, "customer_aggregate"))
        if agg.get("lapsed_180d_plus"):
            out.append(Fact(f"{agg['lapsed_180d_plus']} lapsed patients (180d+)",
                            "derived", 1.4, "customer_aggregate"))
        return out

    def _pick_offer(self, kind: str, payload: dict) -> Optional[dict]:
        active = self._active_offers()
        if active:
            return active[0]
        # fall back to a category-catalog offer that matches the customer's need
        if self.customer:
            services = dig(self.customer, "relationship", "services_received", default=[]) or []
            if services:
                return self._catalog_offer_for(*[str(s).replace("_", " ") for s in services])
        return self._catalog_offer_for("consultation", "cleaning", "trial", "checkup")

    # ---- generic miner: pull any concrete scalar from an arbitrary payload -
    _SKIP_KEYS = {"placeholder", "category", "merchant_id", "customer_id",
                  "top_item_id", "digest_item_id", "ask_template", "last_ask_at",
                  "verification_path", "shelf_action_recommended",
                  "metric_or_topic", "is_imminent", "is_weeknight", "verified",
                  "merchant_last_message", "likely_driver"}

    def _generic_payload_facts(self, p: dict) -> list[Fact]:
        out: list[Fact] = []
        if not isinstance(p, dict):
            return out
        for k, v in p.items():
            if k in self._SKIP_KEYS or v in (None, "", [], {}, True, False):
                continue
            if isinstance(v, (int, float)):
                if "pct" in k or "uplift" in k:
                    out.append(Fact(f"{v*100:+.0f}%" if abs(v) < 1 else f"{v}%", "number", 1.6, "trigger"))
                elif "distance" in k:
                    out.append(Fact(f"{v}km away", "number", 1.8, "trigger"))
                elif "days" in k or "value" in k or "credits" in k:
                    label = k.replace("_", " ")
                    out.append(Fact(f"{v} {label}".replace("days since", "days since").strip(), "number", 1.7, "trigger"))
                else:
                    out.append(Fact(f"{v}", "number", 1.2, "trigger"))
            elif isinstance(v, str):
                if "iso" in k or "date" in k or k.endswith("_at"):
                    out.append(Fact(self._pretty_date(v), "date", 1.6, "trigger"))
                elif len(v) <= 48 and "_" not in v.strip("_"):
                    out.append(Fact(v, "name", 1.3, "trigger"))
                elif "_" in v and len(v) <= 40:
                    out.append(Fact(v.replace("_", " "), "name", 1.0, "trigger"))
        return out

    @staticmethod
    def _pretty_date(iso: str) -> str:
        return iso.split("T")[0] if "T" in iso else iso

    # ---- per-kind extractors (named _facts_<kind>) ------------------------
    def _facts_perf_dip(self, p: dict) -> list[Fact]:
        out = []
        if p.get("delta_pct") is not None and p.get("metric"):
            out.append(Fact(f"{p['metric']} {p['delta_pct']*100:+.0f}% {p.get('window','')}".strip(),
                            "number", 2.8, "performance"))
        if p.get("vs_baseline") is not None:
            out.append(Fact(f"baseline {p['vs_baseline']} {p.get('metric','')}".strip(), "number", 1.6))
        if p.get("likely_driver"):
            out.append(Fact(f"likely driver: {str(p['likely_driver']).replace('_',' ')}", "derived", 1.4))
        return out

    _facts_perf_spike = _facts_perf_dip
    _facts_seasonal_perf_dip = _facts_perf_dip

    def _facts_recall_due(self, p: dict) -> list[Fact]:
        out = []
        if p.get("last_service_date"):
            out.append(Fact(f"last visit {self._pretty_date(p['last_service_date'])}", "date", 2.0))
        if p.get("due_date"):
            out.append(Fact(f"{p.get('service_due','recall').replace('_',' ')} due {self._pretty_date(p['due_date'])}",
                            "date", 2.6))
        for slot in (p.get("available_slots") or [])[:2]:
            if slot.get("label"):
                out.append(Fact(slot["label"], "date", 1.8))
        return out

    def _facts_chronic_refill_due(self, p: dict) -> list[Fact]:
        out = []
        mols = p.get("molecule_list") or []
        if mols:
            out.append(Fact(", ".join(mols), "name", 2.4, "prescription"))
        if p.get("stock_runs_out_iso"):
            out.append(Fact(f"runs out {self._pretty_date(p['stock_runs_out_iso'])}", "date", 2.4))
        if p.get("last_refill"):
            out.append(Fact(f"last refill {self._pretty_date(p['last_refill'])}", "date", 1.4))
        return out

    def _facts_renewal_due(self, p: dict) -> list[Fact]:
        out = []
        if p.get("days_remaining") is not None:
            out.append(Fact(f"{p['days_remaining']} days to renewal", "number", 2.4))
        if p.get("renewal_amount"):
            out.append(Fact(f"₹{p['renewal_amount']:,} {p.get('plan','')} plan".strip(), "number", 1.8))
        return out

    def _facts_milestone_reached(self, p: dict) -> list[Fact]:
        out = []
        metric = (p.get("metric") or "").replace("_", " ")
        if p.get("value_now") is not None:
            out.append(Fact(f"{p['value_now']} {metric}".strip(), "number", 2.4))
        if p.get("milestone_value") is not None:
            out.append(Fact(f"{p['milestone_value']} {metric}".strip() + " milestone", "number", 2.2))
        return out

    def _facts_festival_upcoming(self, p: dict) -> list[Fact]:
        out = []
        if p.get("festival"):
            out.append(Fact(p["festival"], "name", 1.8))
        if p.get("date"):
            out.append(Fact(self._pretty_date(str(p["date"])), "date", 1.8))
        if p.get("days_until") is not None:
            out.append(Fact(f"{p['days_until']} days away", "number", 1.5))
        return out

    def _facts_ipl_match_today(self, p: dict) -> list[Fact]:
        out = []
        if p.get("match"):
            out.append(Fact(str(p["match"]), "name", 2.0))
        if p.get("venue"):
            out.append(Fact(str(p["venue"]), "name", 1.6))
        t = p.get("match_time_iso") or p.get("start_time") or p.get("time")
        if t:
            lbl = t.split("T")[1][:5] if isinstance(t, str) and "T" in t else str(t)
            out.append(Fact(f"{lbl} tonight", "date", 1.8))
        return out

    def _facts_competitor_opened(self, p: dict) -> list[Fact]:
        out = []
        if p.get("distance_km"):
            out.append(Fact(f"{p['distance_km']}km away", "number", 2.2))
        if p.get("competitor_name"):
            out.append(Fact(str(p["competitor_name"]), "name", 1.6))
        if p.get("their_offer"):
            out.append(Fact(f"their offer: {p['their_offer']}", "offer", 1.8))
        if p.get("opened_date"):
            out.append(Fact(f"opened {self._pretty_date(str(p['opened_date']))}", "date", 1.2))
        return out

    def _facts_active_planning_intent(self, p: dict) -> list[Fact]:
        out = []
        if p.get("intent_topic"):
            out.append(Fact(str(p["intent_topic"]).replace("_", " "), "name", 2.0, "merchant"))
        return out

    def _facts_category_seasonal(self, p: dict) -> list[Fact]:
        out = []
        for t in (p.get("trends") or [])[:3]:
            # "ORS_demand_+40" -> "ORS demand +40%"
            pretty = str(t).replace("_", " ")
            if pretty[-3:].lstrip("+-").isdigit() or pretty[-2:].lstrip("+-").isdigit():
                pretty += "%"
            out.append(Fact(pretty, "number", 2.0, "trend"))
        if p.get("season"):
            out.append(Fact(str(p["season"]).replace("_", " "), "name", 1.2))
        return out

    def _facts_cde_opportunity(self, p: dict) -> list[Fact]:
        out = []
        if p.get("credits"):
            out.append(Fact(f"{p['credits']} CDE credits", "number", 1.8))
        if p.get("fee"):
            out.append(Fact(str(p["fee"]).replace("_", " "), "name", 1.0))
        return out

    def _facts_customer_lapsed_hard(self, p: dict) -> list[Fact]:
        out = []
        if p.get("days_since_last_visit"):
            out.append(Fact(f"{p['days_since_last_visit']} days since last visit", "number", 2.2))
        if p.get("previous_focus"):
            out.append(Fact(f"{str(p['previous_focus']).replace('_',' ')} goal", "name", 1.6))
        if p.get("previous_membership_months"):
            out.append(Fact(f"was a {p['previous_membership_months']}-month member", "number", 1.4))
        return out

    _facts_customer_lapsed_soft = _facts_customer_lapsed_hard

    def _facts_dormant_with_vera(self, p: dict) -> list[Fact]:
        out = []
        if p.get("days_since_last_merchant_message"):
            out.append(Fact(f"{p['days_since_last_merchant_message']} days since we last spoke", "number", 1.8))
        if p.get("last_topic"):
            out.append(Fact(f"last on {str(p['last_topic']).replace('_',' ')}", "name", 1.2))
        return out

    def _facts_gbp_unverified(self, p: dict) -> list[Fact]:
        out = []
        if p.get("estimated_uplift_pct"):
            out.append(Fact(f"~{p['estimated_uplift_pct']*100:.0f}% more visibility once verified", "number", 2.0))
        return out

    def _facts_review_theme_emerged(self, p: dict) -> list[Fact]:
        out = []
        if p.get("theme"):
            out.append(Fact(f"\"{p['theme'].replace('_',' ')}\" theme", "name", 1.8))
        if p.get("occurrences") or p.get("occurrences_30d"):
            n = p.get("occurrences") or p.get("occurrences_30d")
            out.append(Fact(f"{n} mentions this week", "number", 2.0))
        return out

    def _facts_supply_alert(self, p: dict) -> list[Fact]:
        out = []
        for b in (p.get("batches") or []):
            out.append(Fact(str(b), "name", 2.2))
        if p.get("molecule"):
            out.append(Fact(str(p["molecule"]), "name", 1.8))
        agg = self.merchant.get("customer_aggregate", {}) or {}
        if agg.get("chronic_rx_count") or agg.get("total_unique_ytd"):
            n = agg.get("chronic_rx_count") or agg.get("total_unique_ytd")
            out.append(Fact(f"{n} chronic-Rx customers on file", "derived", 1.6))
        return out

    def _facts_wedding_package_followup(self, p: dict) -> list[Fact]:
        out = []
        if p.get("days_to_wedding") is not None:
            out.append(Fact(f"{p['days_to_wedding']} days to your wedding", "number", 2.4))
        if p.get("wedding_date"):
            out.append(Fact(str(p["wedding_date"]), "date", 1.8))
        if p.get("trial_completed"):
            out.append(Fact(f"trial done {p['trial_completed']}", "date", 1.5))
        return out

    _facts_trial_followup = _facts_wedding_package_followup
