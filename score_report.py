"""Offline score estimate — no API key needed.

Runs the internal judge model (src/scoring.py, a heuristic mirror of the real
5-dimension rubric + penalties) over every canonical test pair and prints a
per-message and average estimate. This is a development aid, NOT the official
score — the real judge is a frontier LLM. Use it to catch penalties and weak
messages before submitting.

    python score_report.py --dataset ../dataset/expanded
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bot import compose                       # noqa: E402
from src.context_engine import Resolver       # noqa: E402
from src import scoring                        # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")   # avoid Windows cp1252 crashes
except Exception:
    pass


def _load(path: str, key: str) -> dict:
    out = {}
    for f in glob.glob(os.path.join(path, "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        out[d.get(key, os.path.basename(f))] = d
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="../dataset/expanded")
    args = ap.parse_args()
    root = args.dataset

    cats = _load(os.path.join(root, "categories"), "slug")
    ms = _load(os.path.join(root, "merchants"), "merchant_id")
    cs = _load(os.path.join(root, "customers"), "customer_id")
    ts = _load(os.path.join(root, "triggers"), "id")
    pairs = json.load(open(os.path.join(root, "test_pairs.json"), encoding="utf-8"))["pairs"]

    print(f"{'id':<5}{'spec':>5}{'cat':>5}{'merc':>6}{'dec':>5}{'eng':>5}{'pen':>5}{'TOT':>6}  message")
    print("-" * 100)
    totals, penalties = [], 0
    dims = {"spec": 0, "cat": 0, "merc": 0, "dec": 0, "eng": 0}
    for p in pairs:
        t = ts.get(p["trigger_id"], {})
        m = ms.get(p["merchant_id"], {})
        c = cats.get(m.get("category_slug", ""), {})
        cust = cs.get(p["customer_id"]) if p.get("customer_id") else None
        rc = Resolver(c, m, t, cust).resolve()
        msg = compose(c, m, t, cust)
        s = scoring.score(msg["body"], msg["cta"], rc, msg.get("levers", []))
        totals.append(s.total)
        penalties += s.penalties
        dims["spec"] += s.specificity; dims["cat"] += s.category_fit
        dims["merc"] += s.merchant_fit; dims["dec"] += s.decision_quality
        dims["eng"] += s.engagement
        flag = " ⚠" + ",".join(s.issues) if s.issues else ""
        print(f"{p['test_id']:<5}{s.specificity:>5}{s.category_fit:>5}{s.merchant_fit:>6}"
              f"{s.decision_quality:>5}{s.engagement:>5}{s.penalties:>5}{s.total:>6}  "
              f"{msg['body'][:60]}{flag}")

    n = len(totals)
    print("-" * 100)
    print(f"AVG TOTAL {sum(totals)/n:.1f}/50   "
          f"(spec {dims['spec']/n:.1f}, cat {dims['cat']/n:.1f}, merc {dims['merc']/n:.1f}, "
          f"dec {dims['dec']/n:.1f}, eng {dims['eng']/n:.1f})")
    print(f"min {min(totals)}  max {max(totals)}  total penalties: {penalties}")
    print("\nNOTE: heuristic estimate only. The official judge is an LLM — run "
          "judge_simulator.py with a working API key for the real score.")


if __name__ == "__main__":
    main()
