"""Shared fixtures: realistic 4-context tuples mirroring the dataset shapes."""
import sys
from pathlib import Path

import pytest

# make the project importable when pytest is run from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def dentist_category():
    return {
        "slug": "dentists",
        "voice": {"tone": "peer_clinical",
                  "vocab_allowed": ["fluoride varnish", "caries", "scaling"],
                  "vocab_taboo": ["guaranteed", "100% safe", "miracle"]},
        "peer_stats": {"scope": "metro_solo_practices_2026", "avg_ctr": 0.030,
                       "avg_review_count": 62},
        "offer_catalog": [
            {"id": "den_001", "title": "Dental Cleaning @ ₹299", "type": "service_at_price"}],
        "digest": [
            {"id": "d_jida", "kind": "research",
             "title": "3-month fluoride recall cuts caries 38% better than 6-month",
             "source": "JIDA Oct 2026, p.14", "trial_n": 2100,
             "summary": "38% lower recurrence in high-risk adults."}],
    }


@pytest.fixture
def dentist_merchant():
    return {
        "merchant_id": "m_001_drmeera",
        "category_slug": "dentists",
        "identity": {"name": "Dr. Meera's Dental Clinic", "city": "Delhi",
                     "locality": "Lajpat Nagar", "languages": ["en", "hi"],
                     "owner_first_name": "Meera"},
        "performance": {"views": 2410, "calls": 18, "ctr": 0.021,
                        "delta_7d": {"views_pct": 0.18}},
        "offers": [{"id": "o1", "title": "Dental Cleaning @ ₹299", "status": "active"}],
        "customer_aggregate": {"high_risk_adult_count": 124, "lapsed_180d_plus": 78},
        "signals": ["stale_posts:22d", "ctr_below_peer_median", "high_risk_adult_cohort"],
    }


@pytest.fixture
def research_trigger():
    return {
        "id": "trg_research", "scope": "merchant", "kind": "research_digest",
        "source": "external", "merchant_id": "m_001_drmeera",
        "payload": {"category": "dentists", "top_item_id": "d_jida"},
        "urgency": 2, "suppression_key": "research:dentists:2026-W17",
        "expires_at": "2026-12-31T00:00:00Z",
    }


@pytest.fixture
def recall_trigger():
    return {
        "id": "trg_recall", "scope": "customer", "kind": "recall_due",
        "source": "internal", "merchant_id": "m_001_drmeera",
        "customer_id": "c_priya",
        "payload": {"service_due": "6_month_cleaning", "last_service_date": "2026-05-12",
                    "due_date": "2026-11-12",
                    "available_slots": [{"iso": "2026-11-05T18:00", "label": "Wed 5 Nov, 6pm"},
                                        {"iso": "2026-11-06T17:00", "label": "Thu 6 Nov, 5pm"}]},
        "urgency": 3, "suppression_key": "recall:c_priya:6mo",
        "expires_at": "2026-12-31T00:00:00Z",
    }


@pytest.fixture
def priya_customer():
    return {
        "customer_id": "c_priya", "merchant_id": "m_001_drmeera",
        "identity": {"name": "Priya", "language_pref": "hi-en mix", "age_band": "25-35"},
        "relationship": {"last_visit": "2026-05-12", "visits_total": 4,
                         "services_received": ["cleaning", "whitening"]},
        "state": "lapsed_soft",
        "preferences": {"preferred_slots": "weekday_evening", "channel": "whatsapp"},
    }
