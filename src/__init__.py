"""Vera — magicpin Merchant AI Assistant (challenge submission).

A modular, fact-grounded engagement engine that composes WhatsApp messages
for merchants (and their customers) from the 4-context framework:
CategoryContext, MerchantContext, TriggerContext, CustomerContext.

The package is intentionally split into single-responsibility engines so the
behaviour can be reasoned about, unit-tested, and extended without touching the
HTTP surface:

    context_engine        — versioned context store + resolution/hydration
    trigger_engine        — prioritisation, urgency, relevance, suppression
    merchant_intelligence — profiling + peer benchmarking + derived insight
    engagement_engine     — compulsion levers + CTA optimisation
    composer              — fact+insight+action+cta message assembly (+LLM polish)
    conversation_engine   — intent detection, memory, follow-ups, send/wait/end
    auto_reply_detector   — auto-reply / OOO / generic-ack / spam detection
    scoring               — internal judge model (self-critique + variant selection)
    llm                   — Gemini client with timeout + deterministic fallback
    app                   — FastAPI wiring of the 5 challenge endpoints
"""

__version__ = "1.0.0"
