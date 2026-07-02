"""Conversation engine: auto-reply, intent transition, hostile exit, anti-repetition."""
from src.context_engine import ContextStore
from src.trigger_engine import TriggerEngine
from src.conversation_engine import ConversationEngine
from src import auto_reply_detector as ard


def _engine():
    store = ContextStore()
    te = TriggerEngine(store)
    return ConversationEngine(store, te), te


def test_auto_reply_detected_and_classified():
    assert ard.classify("Thank you for contacting us! Our team will respond shortly.").label == "auto_reply"
    assert ard.classify("Out of office until Monday").label == "ooo"
    assert ard.classify("ok").label == "generic_ack"
    assert ard.classify("Yes please send the abstract, sounds great").label == "real"


def test_auto_reply_hell_escalates_to_end():
    eng, _ = _engine()
    auto = "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly."
    actions = []
    for i in range(1, 5):
        r = eng.handle_reply("conv1", "m_001", auto, i + 1)
        actions.append(r.action)
    # first a gentle send/wait, eventually an end — never an infinite send loop
    assert actions[-1] == "end"
    assert "send" not in actions[2:]  # no wasted turns after escalation


def test_auto_reply_hell_escalates_even_with_fresh_conversation_ids():
    """The bundled judge_simulator uses a new conversation_id each turn."""
    eng, _ = _engine()
    auto = "Thank you for contacting us! Our team will respond shortly."
    last = None
    for i in range(1, 5):
        last = eng.handle_reply(f"conv_auto_{i}", "m_001", auto, i + 1)
    assert last.action in ("end", "wait")  # detected via global repetition tracker


def test_intent_transition_switches_to_action_mode():
    eng, _ = _engine()
    eng.register("c1", "m_001", None, None, "pitch", prepared_action="draft the post")
    r = eng.handle_reply("c1", "m_001", "Ok lets do it. Whats next?", 2)
    assert r.action == "send"
    low = r.body.lower()
    actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next", "on it"]
    qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
    assert any(w in low for w in actioning)
    assert not any(w in low for w in qualifying)


def test_hostile_message_ends_and_suppresses():
    eng, te = _engine()
    r = eng.handle_reply("c1", "m_001", "This is useless spam. Stop messaging me.", 2)
    assert r.action == "end"
    # merchant is suppressed for future ticks
    from datetime import datetime, timezone
    assert te._is_merchant_suppressed("m_001", datetime.now(timezone.utc))


def test_offtopic_redirects_without_losing_thread():
    eng, _ = _engine()
    eng.register("c1", "m_001", None, None, "pitch", prepared_action="draft the post")
    r = eng.handle_reply("c1", "m_001", "Btw can you also help me file my GST?", 2)
    assert r.action == "send"
    assert "gst" not in r.body.lower() or "outside" in r.body.lower() or "ca" in r.body.lower()


def test_anti_repetition_never_repeats_body():
    eng, _ = _engine()
    eng.register("c1", "m_001", None, None, "pitch", prepared_action="draft the post")
    seen = set()
    for msg in ["yes", "ok", "sure"]:
        r = eng.handle_reply("c1", "m_001", msg, 2)
        if r.action == "send" and r.body:
            assert r.body not in seen
            seen.add(r.body)


def test_decline_opts_out():
    eng, _ = _engine()
    r = eng.handle_reply("c1", "m_001", "not interested, unsubscribe", 2)
    assert r.action == "end"
