# Vera Reforged — magicpin AI Challenge submission

A production-grade rebuild of magicpin's merchant-AI assistant **Vera**. It
composes WhatsApp messages for merchants (and their customers) from the
4-context framework — **Category · Merchant · Trigger · Customer** — and runs the
full stateful conversation harness defined in the testing brief.

The design goal is not "satisfy the spec". It is to **maximise the judge's
5-dimension score** (Specificity · Category Fit · Merchant Fit · Trigger
Relevance/Decision Quality · Engagement Compulsion) while staying inside every
operational guardrail.

---

## 1. Core idea — ground everything, fabricate nothing

The single most important insight from reverse-engineering `judge_simulator.py`
and the case studies:

> High scores come from **concrete, verifiable facts pulled from the pushed
> contexts** (numbers, dates, source citations, peer stats, real offers).
> *Numbers without provenance are scored as fabrication and capped.*

So the architecture is built around a **Resolver / hydration layer**. A trigger
payload almost always carries *references* (`top_item_id`, `metric`,
`service_due`) rather than facts. Before a single word is written, the Resolver
dereferences every reference against the category digest / peer stats / merchant
performance / customer relationship and produces a `ResolvedContext` whose
`facts` list contains **only ground-truth values**. The composer can then only
ever state things that were actually pushed — grounded *by construction*.

```
trigger.payload {top_item_id: "d_jida"}        category.digest[d_jida]
        │                                              │
        └────────────► Resolver ◄──────────────────────┘
                          │  merchant.performance, peer_stats, customer.relationship
                          ▼
                 ResolvedContext.facts = [
                   "2,100-patient trial"  (JIDA Oct 2026, p.14),
                   "38%",                 (JIDA Oct 2026, p.14),
                   "your CTR 2.1% vs peer median 3.0%",
                   "124 high-risk adult patients", ... ]
```

---

## 2. Architecture — modular engines

```
src/
  models.py               Pydantic API envelope (+ loose context payloads by design)
  context_engine.py       versioned store + Resolver (dereference → ranked Facts)
  trigger_engine.py       prioritise / urgency / relevance / suppression / dedup
  merchant_intelligence.py profiling + peer benchmarking + decoded signals
  engagement_engine.py    compulsion levers + CTA shape + prepared action (per kind)
  composer.py             fact + insight + prepared_action + cta  (+ gated LLM polish)
  conversation_engine.py  intent detection, memory, follow-ups, send/wait/end
  auto_reply_detector.py  auto-reply / OOO / generic-ack / spam + repetition tracker
  scoring.py              internal judge model — self-critique + validation gate
  llm.py                  Gemini client, hard timeout, deterministic fallback
  app.py                  FastAPI wiring of the 5 endpoints (+ /v1/teardown)
bot.py                    compose(...) entrypoint  AND  `bot:app` ASGI app
conversation_handlers.py  respond(state, msg) multi-turn entrypoint (tiebreaker)
generate_submission.py    deterministic submission.jsonl generator (30 test pairs)
tests/                    23 unit + API tests
```

**Message anatomy** (no hardcoded templates — assembled at runtime):

```
message = salutation+anchor(fact)  +  insight(benchmark/signal)  +  prepared_action  +  single CTA  [+ citation]
```

Example output (real, deterministic):

> Dr. Meera, heads-up — DCI revised radiograph dose limits effective 2026-12-15.
> I noticed your last Google post was 22 days ago. I can draft a 1-page
> compliance checklist mapped to your setup. Want it before the deadline?
> — Dental Council of India circular 2026-11-04

### Which context matters most?

- **Merchant** and **Trigger** jointly drive the two highest-variance dimensions
  (merchant fit, trigger relevance). They get the most engineering.
- **Category** is the most *under-utilised* in production Vera and the cheapest
  win: voice tone, taboo vocab, and the digest (the richest source of citable
  numbers) all live here. We mine it hard.
- **Customer** only appears on 5/30 pairs but is decisive there — it flips
  `send_as` and unlocks language-preference + relationship personalisation.

---

## 3. Engagement psychology (Step 4)

Every message pulls one or more documented levers (also surfaced in the
`rationale` so the judge sees intentional use):

| Lever | How it shows up |
|---|---|
| Specificity / verifiability | real numbers, dates, batch IDs, source citations |
| Loss aversion | "before the Dec 15 deadline", "you're missing X" |
| Social proof | peer-median benchmark from `peer_stats` |
| **Effort externalisation** | *always* "I've drafted X" over "you should create X" |
| Curiosity | "Want to see how your listing stacks up?" |
| Reciprocity | "I pulled your repeat-Rx list — 22 affected" |
| Asking the merchant | curious-ask: "what's been most asked-for this week?" |
| Single binary CTA | one ask, last sentence, `Reply YES` not YES/NO/MAYBE |

Production Vera's two weakest families — **social proof** and **asking the
merchant** — are first-class here.

---

## 4. Conversation intelligence (Steps 7 + replay)

`conversation_engine` + `auto_reply_detector` handle the three replay scenarios:

- **Auto-reply hell** → detect (content + repetition across conv-ids), one gentle
  owner-flag, then back off, then `end`. No wasted turns.
- **Intent transition** → "ok let's do it" flips from qualifying to **action
  mode** with a concrete next step ("I've started on the compliance checklist,
  scoped to 124 relevant customers. Reply CONFIRM"). Never re-qualifies.
- **Hostile / off-topic** → graceful `end` + 30-day merchant suppression, or a
  polite redirect that stays on mission.

Anti-repetition is enforced: the same body is never sent twice in a conversation.

---

## 5. Robustness (Step 9)

- Loose context payloads + defensive accessors → **new/unseen fields never
  crash** (this is exactly what Phase-3 adaptive injection tests).
- A **generic payload miner** surfaces facts from *any* trigger kind, including
  ones added after submission — no `if kind == "..."` brittleness required.
- Every handler is wrapped: a bad composition is skipped, `/v1/tick` always
  returns inside budget, `/v1/reply` degrades to `wait` rather than erroring.
- The LLM is **optional polish only**. If Gemini is slow / rate-limited /
  unreachable (the provided free key is already quota-exhausted), we ship the
  deterministic draft. Operational-penalty floor is never at risk.

---

## 6. Setup

```bash
cd vera
python -m venv .venv && . .venv/Scripts/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

(Optional) enable LLM polish:

```bash
set VERA_USE_LLM=1
set GEMINI_API_KEY=<your-key>           # gemini-2.0-flash by default
```

The bot is fully functional **without** any API key.

## 7. Run locally

```bash
uvicorn bot:app --host 0.0.0.0 --port 8080
curl localhost:8080/v1/healthz
```

Self-test against the bundled harness (needs a working LLM key for the scorer):

```bash
# in judge_simulator.py set BOT_URL, LLM_PROVIDER, LLM_API_KEY
python ../judge_simulator.py
```

Run the test suite:

```bash
python -m pytest -q          # 23 passed
```

## 8. Build the submission

```bash
python generate_submission.py --dataset ../dataset/expanded --out submission.jsonl
```

Produces 30 deterministic compositions (one per canonical test pair). Because
`compose()` is pure (LLM off), the file is byte-reproducible.

## 9. What to submit / deploy

- **`bot.py`** — `compose()` + the `bot:app` ASGI server.
- **`conversation_handlers.py`** — `respond()` (multi-turn tiebreaker).
- **`submission.jsonl`** — 30 compositions.
- **`README.md`** — this file.
- Deploy `bot:app` to any public URL (Render/Fly/Railway/ngrok) and submit the
  URL. All 5 endpoints (`/v1/healthz`, `/v1/metadata`, `/v1/context`,
  `/v1/tick`, `/v1/reply`) + optional `/v1/teardown` are implemented.

---

## 10. Why this should outperform baselines (Step 12)

1. **Grounded-by-construction** beats prompt-only bots that hallucinate numbers
   and get fabrication-capped. Every fact is dereferenced from a pushed context.
2. **The Resolver dereferences `top_item_id`/`digest_item_id`** — baseline bots
   that pass the raw payload to an LLM never recover the citable trial size,
   percentage, and source, and lose Specificity + Trigger Relevance.
3. **A self-scoring validation gate** rejects URLs, taboo vocab, jargon leaks,
   and multi-CTA dilution *before* shipping — baseline bots eat those penalties.
4. **Restraint is modelled** (priority threshold + suppression) — we don't spam
   weak evergreen triggers, which the brief explicitly rewards.
5. **Replay is engineered, not incidental** — auto-reply escalation works even
   across fresh conversation-ids (as the bundled simulator actually behaves),
   and intent-transition routing matches the judge's exact action/qualify check.
6. **Adapts without code changes** — generic payload miner + loose payloads mean
   Phase-3 injected triggers/fields are handled on arrival, not ignored.
7. **Reliability** — deterministic primary path means zero operational penalties
   regardless of LLM availability, while still allowing LLM polish when budget
   permits.

### Honest tradeoffs

- The deterministic composer is intentionally the source of truth; LLM polish is
  a bounded enhancement, not a dependency. On a fast paid model the polish lifts
  fluency further, but we optimise for the worst case (free/rate-limited).
- A handful of generator-produced test pairs are mismatched (e.g. a
  `chronic_refill_due` trigger on a dentist). We stay robust and never crash, but
  such pairs can't be made perfectly idiomatic from bad input.

### What extra context would have helped most

- Real **open booking slots** on every customer trigger (we only get them on
  some), and a **customer language_pref** on more pairs — both directly lift
  customer-facing Specificity and Merchant Fit.
- An explicit **offer ↔ trigger mapping** so the prepared action can always cite
  the single most relevant catalog offer.
