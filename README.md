# Magicpin AI Challenge - Vera

FastAPI implementation of **Vera**, a merchant AI assistant for the magicpin AI
Challenge. The bot accepts category, merchant, customer, and trigger context,
then generates grounded WhatsApp-style merchant/customer messages.

Author: **Divyanshu Jangid**  
GitHub: https://github.com/divyanshujangid2018

## What This Project Does

- Stores pushed context through `/v1/context`
- Selects relevant triggers through `/v1/tick`
- Generates merchant/customer messages from grounded facts
- Handles follow-up replies through `/v1/reply`
- Provides a small frontend/status page at `/`
- Runs locally and deploys cleanly on Render

## Tech Stack

- Python 3.12
- FastAPI
- Uvicorn
- Pydantic
- Pytest

## Project Structure

```text
.
├── bot.py                    # Main entrypoint: compose(...) and bot:app
├── conversation_handlers.py  # Multi-turn conversation entrypoint
├── generate_submission.py    # Generates submission.jsonl
├── Procfile                  # Render start command
├── requirements.txt          # Python dependencies
├── src/
│   ├── app.py                # FastAPI routes
│   ├── composer.py           # Message composition
│   ├── context_engine.py     # Context store and resolver
│   ├── conversation_engine.py
│   ├── trigger_engine.py
│   └── models.py
└── tests/                    # API and behavior tests
```

## Run Locally

Clone the repository:

```bash
git clone https://github.com/divyanshujangid2018/magicpin_AI_Challenge.git
cd magicpin_AI_Challenge
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the server:

```bash
uvicorn bot:app --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080
```

Health check:

```bash
curl http://127.0.0.1:8080/v1/healthz
```

Expected response:

```json
{
  "status": "ok"
}
```

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Simple frontend/status page |
| GET | `/v1/healthz` | Health check |
| GET | `/v1/metadata` | Team and model metadata |
| POST | `/v1/context` | Push category/merchant/customer/trigger context |
| POST | `/v1/tick` | Generate outbound actions |
| POST | `/v1/reply` | Handle merchant/customer replies |
| POST | `/v1/teardown` | Reset in-memory state |

## Quick Curl Test

Set the base URL:

```bash
BASE="http://127.0.0.1:8080"
```

Push category context:

```bash
curl -X POST "$BASE/v1/context" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "category",
    "context_id": "dentists",
    "version": 1,
    "payload": {
      "slug": "dentists",
      "peer_stats": {"avg_ctr": 0.03},
      "digest": [
        {
          "id": "d_jida",
          "kind": "research",
          "title": "3-month fluoride recall cuts caries 38% better than 6-month",
          "source": "JIDA Oct 2026, p.14",
          "trial_n": 2100,
          "summary": "38% lower recurrence in high-risk adults."
        }
      ]
    }
  }'
```

Push merchant context:

```bash
curl -X POST "$BASE/v1/context" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "merchant",
    "context_id": "m_001_drmeera",
    "version": 1,
    "payload": {
      "merchant_id": "m_001_drmeera",
      "category_slug": "dentists",
      "identity": {
        "name": "Dr. Meera Dental Clinic",
        "city": "Delhi",
        "locality": "Lajpat Nagar",
        "owner_first_name": "Meera"
      },
      "performance": {
        "views": 2410,
        "calls": 18,
        "ctr": 0.021
      },
      "customer_aggregate": {
        "high_risk_adult_count": 124
      },
      "signals": [
        "stale_posts:22d",
        "ctr_below_peer_median"
      ]
    }
  }'
```

Push trigger context:

```bash
curl -X POST "$BASE/v1/context" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "trigger",
    "context_id": "trg_research",
    "version": 1,
    "payload": {
      "id": "trg_research",
      "scope": "merchant",
      "kind": "research_digest",
      "merchant_id": "m_001_drmeera",
      "payload": {
        "category": "dentists",
        "top_item_id": "d_jida"
      },
      "urgency": 2,
      "suppression_key": "research:dentists:2026-W17",
      "expires_at": "2026-12-31T00:00:00Z"
    }
  }'
```

Generate a message:

```bash
curl -X POST "$BASE/v1/tick" \
  -H "Content-Type: application/json" \
  -d '{
    "now": "2026-04-26T10:35:00Z",
    "available_triggers": ["trg_research"]
  }'
```

## Run Tests

```bash
python -m pytest -q
```

Current local result:

```text
23 passed
```

## Deploy On Render

This repository is ready for Render.

Use these settings:

```text
Service type: Web Service
Language: Python
Build Command: pip install -r requirements.txt
Start Command: uvicorn bot:app --host 0.0.0.0 --port $PORT
```

Optional environment variables:

```text
VERA_TEAM_NAME=Vera Reforged
VERA_TEAM_MEMBER=Divyanshu Jangid
VERA_CONTACT=your_email_here
```

After deployment, test:

```bash
curl https://YOUR-RENDER-URL.onrender.com/v1/healthz
```

## Submission Files

Important files for the challenge:

- `bot.py`
- `conversation_handlers.py`
- `submission.jsonl`
- `requirements.txt`
- `README.md`

## Notes

The app runs without any LLM API key. LLM polishing is optional; the default
behavior is deterministic and grounded in the pushed context.
