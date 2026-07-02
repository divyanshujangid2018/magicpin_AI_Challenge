"""End-to-end API contract tests via FastAPI TestClient."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # fresh module each test so the in-memory store starts empty
    import src.app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _push(client, scope, cid, payload, version=1):
    return client.post("/v1/context", json={
        "scope": scope, "context_id": cid, "version": version,
        "payload": payload, "delivered_at": "2026-04-26T10:00:00Z"})


def test_healthz_and_counts(client, dentist_category, dentist_merchant):
    assert client.get("/v1/healthz").json()["status"] == "ok"
    _push(client, "category", "dentists", dentist_category)
    _push(client, "merchant", "m_001_drmeera", dentist_merchant)
    counts = client.get("/v1/healthz").json()["contexts_loaded"]
    assert counts["category"] == 1 and counts["merchant"] == 1


def test_metadata_shape(client):
    md = client.get("/v1/metadata").json()
    for k in ("team_name", "model", "approach", "version"):
        assert k in md


def test_context_idempotency_and_versioning(client, dentist_merchant):
    assert _push(client, "merchant", "m_001_drmeera", dentist_merchant, 1).json()["accepted"]
    # same version -> 409 stale
    r = _push(client, "merchant", "m_001_drmeera", dentist_merchant, 1)
    assert r.status_code == 409 and r.json()["accepted"] is False
    # higher version -> replaces
    assert _push(client, "merchant", "m_001_drmeera", dentist_merchant, 2).json()["accepted"]


def test_tick_composes_grounded_action(client, dentist_category, dentist_merchant, research_trigger):
    _push(client, "category", "dentists", dentist_category)
    _push(client, "merchant", "m_001_drmeera", dentist_merchant)
    _push(client, "trigger", "trg_research", research_trigger)
    r = client.post("/v1/tick", json={"now": "2026-04-26T10:35:00Z",
                                       "available_triggers": ["trg_research"]})
    actions = r.json()["actions"]
    assert len(actions) == 1
    a = actions[0]
    for field in ("conversation_id", "send_as", "trigger_id", "cta",
                  "suppression_key", "rationale", "body"):
        assert a.get(field) not in (None, "")
    assert "JIDA" in a["body"]


def test_tick_is_restrained_with_no_triggers(client):
    r = client.post("/v1/tick", json={"now": "2026-04-26T10:35:00Z", "available_triggers": []})
    assert r.json()["actions"] == []


def test_reply_auto_reply_backs_off(client):
    r = client.post("/v1/reply", json={
        "conversation_id": "c1", "merchant_id": "m_001_drmeera", "from_role": "merchant",
        "message": "Thank you for contacting us! Our team will respond shortly.",
        "received_at": "2026-04-26T10:42:00Z", "turn_number": 2})
    assert r.json()["action"] in ("send", "wait", "end")


def test_suppression_prevents_resend(client, dentist_category, dentist_merchant, research_trigger):
    _push(client, "category", "dentists", dentist_category)
    _push(client, "merchant", "m_001_drmeera", dentist_merchant)
    _push(client, "trigger", "trg_research", research_trigger)
    body = {"now": "2026-04-26T10:35:00Z", "available_triggers": ["trg_research"]}
    first = client.post("/v1/tick", json=body).json()["actions"]
    second = client.post("/v1/tick", json=body).json()["actions"]
    assert len(first) == 1 and len(second) == 0  # suppression_key dedups
