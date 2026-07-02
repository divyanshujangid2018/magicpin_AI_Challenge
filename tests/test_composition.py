"""Composition + resolution + scoring guardrails."""
from src.context_engine import ContextStore, Resolver
from src.composer import Composer
from src import scoring

composer = Composer()


def _compose(cat, merch, trig, cust=None):
    rc = Resolver(cat, merch, trig, cust).resolve()
    return rc, composer.compose(rc)


def test_research_digest_is_grounded_and_cited(dentist_category, dentist_merchant, research_trigger):
    rc, msg = _compose(dentist_category, dentist_merchant, research_trigger)
    b = msg.body
    assert "Dr. Meera" in b                      # owner first name + Dr. prefix
    assert "JIDA Oct 2026, p.14" in b            # real source citation
    assert "38%" in b or "2,100" in b or "2100" in b  # a real number from the digest
    assert "http" not in b                        # no URLs
    assert msg.send_as == "vera"


def test_no_double_doctor_prefix():
    cat = {"slug": "dentists", "voice": {}}
    merch = {"merchant_id": "m", "category_slug": "dentists",
             "identity": {"name": "X", "owner_first_name": "Dr. Sameer", "languages": ["en"]},
             "performance": {}, "offers": [], "signals": []}
    trig = {"id": "t", "kind": "perf_spike", "merchant_id": "m",
            "payload": {"metric": "calls", "delta_pct": 0.2}, "suppression_key": "s"}
    _, msg = _compose(cat, merch, trig)
    assert "Dr. Dr." not in msg.body


def test_customer_facing_honours_language_and_sends_on_behalf(
        dentist_category, dentist_merchant, recall_trigger, priya_customer):
    _, msg = _compose(dentist_category, dentist_merchant, recall_trigger, priya_customer)
    assert msg.send_as == "merchant_on_behalf"
    assert "Priya" in msg.body
    assert "Wed 5 Nov, 6pm" in msg.body          # real slot label
    assert msg.cta == "multi_choice_slot"


def test_taboo_words_never_appear(dentist_category, dentist_merchant, research_trigger):
    rc, msg = _compose(dentist_category, dentist_merchant, research_trigger)
    low = msg.body.lower()
    for taboo in ("guaranteed", "100% safe", "miracle"):
        assert taboo not in low


def test_internal_jargon_never_leaks(dentist_category, dentist_merchant, research_trigger):
    rc, msg = _compose(dentist_category, dentist_merchant, research_trigger)
    assert not any(j in msg.body.lower() for j in
                   ("suppression_key", "ctr_below_peer_median", "stale_posts:", "merchant_id"))


def test_scoring_rewards_grounded_message(dentist_category, dentist_merchant, research_trigger):
    rc, msg = _compose(dentist_category, dentist_merchant, research_trigger)
    c = scoring.score(msg.body, msg.cta, rc, msg.levers)
    assert c.penalties == 0
    assert c.specificity >= 7
    assert c.total >= 30


def test_missing_context_does_not_crash():
    # empty everything — must still return a non-empty, valid message
    rc = Resolver({}, {"merchant_id": "m", "identity": {}}, {"kind": "", "payload": {}}, None).resolve()
    msg = composer.compose(rc)
    assert isinstance(msg.body, str) and msg.body.strip()
    assert "http" not in msg.body


def test_rationale_is_populated(dentist_category, dentist_merchant, research_trigger):
    _, msg = _compose(dentist_category, dentist_merchant, research_trigger)
    assert len(msg.rationale) > 20
    assert "research_digest" in msg.rationale
