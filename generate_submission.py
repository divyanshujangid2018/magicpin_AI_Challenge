"""Generate submission.jsonl deterministically from the canonical 30 test pairs.

Usage:
    python generate_submission.py \
        --dataset ../dataset/expanded \
        --out submission.jsonl

Loads the expanded dataset + test_pairs.json, composes a message for each pair
using the (offline, deterministic) composer, and writes one JSONL line per pair
with the exact fields the challenge expects:
    test_id, body, cta, send_as, suppression_key, rationale
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot import compose


def _load_dir(path: Path, key_field: str) -> dict[str, dict]:
    """Load every JSON in `path`, keyed by the scope's identifier field.

    Scope-aware keying matters: a trigger payload also carries `merchant_id`, so
    keying by "first id-looking field" would collide triggers onto merchants.
    """
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for f in path.glob("*.json"):
        data = json.load(open(f, encoding="utf-8"))
        key = data.get(key_field) or f.stem
        out[key] = data
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="../dataset/expanded")
    ap.add_argument("--out", default="submission.jsonl")
    args = ap.parse_args()

    root = Path(args.dataset)
    categories = _load_dir(root / "categories", "slug")
    merchants = _load_dir(root / "merchants", "merchant_id")
    customers = _load_dir(root / "customers", "customer_id")
    triggers = _load_dir(root / "triggers", "id")
    pairs = json.load(open(root / "test_pairs.json", encoding="utf-8"))["pairs"]

    lines = []
    for pair in pairs:
        trig = triggers.get(pair["trigger_id"], {})
        merch = merchants.get(pair["merchant_id"], {})
        cat = categories.get(merch.get("category_slug", ""), {})
        cust = customers.get(pair["customer_id"]) if pair.get("customer_id") else None

        result = compose(cat, merch, trig, cust)
        lines.append({
            "test_id": pair["test_id"],
            "body": result["body"],
            "cta": result["cta"],
            "send_as": result["send_as"],
            "suppression_key": result["suppression_key"],
            "rationale": result["rationale"],
        })

    with open(args.out, "w", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(json.dumps(ln, ensure_ascii=False) + "\n")
    print(f"Wrote {len(lines)} compositions to {args.out}")


if __name__ == "__main__":
    main()
