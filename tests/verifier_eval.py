"""
A larger live check of the verifier, on real claims from earlier runs plus false variants made from them.

    backend\\.venv\\Scripts\\python.exe tests\\verifier_eval.py [--url https://fnms-verifier.onrender.com]

1. Takes every distinct (claim text, source URL) the orchestrator saved in `tracker_claims` with a
   verifier verdict (supported / unsupported / unreachable), excluding the FAULT INJECTION test claims.
2. For each claim that contains a number, makes a FALSE VARIANT by changing one digit of its first
   number (2026 -> 2027, 1.7 -> 1.8, 4 -> 5). By construction the variant says something its source
   doesn't, so the right verdict is "unsupported" (or "unreachable" if the page can't be fetched).
   Caveat: the changed number could, by coincidence, also appear on the page in a matching context.
3. Sends all of them to the verifier (grouped so each URL's claims go in one request, so the false
   variant is judged next to its original) and records every verdict.

What it can and can't measure:
  - false variants: truth known by construction -> a real error rate for "accepting a false claim";
  - originals: nobody re-checked them by hand for this test, so the result is AGREEMENT with the
    earlier verdict, not accuracy.
Uses the real model (about one call per distinct URL). Writes traces/a2-tests/verifier-eval-<date>.json.
"""
import argparse
import json
import os
import re
import sys
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def load_env() -> None:
    for p in (ROOT / ".env",):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def load_claims() -> list[dict]:
    from sqlalchemy import text  # noqa: E402
    from app.db import engine  # noqa: E402   (reads backend/.env)
    q = text("""select text, source_url, verdict, min(claim_id) as claim_id
                from tracker_claims
                where text not like 'FAULT INJECTION%' and verdict in ('supported','unsupported','unreachable')
                group by text, source_url, verdict order by source_url, min(claim_id)""")
    with engine.connect() as c:
        rows = c.execute(q).all()
    seen, out = set(), []
    for t, url, verdict, _cid in rows:
        if (t, url) in seen:      # same claim stored with two verdicts in two runs: keep the first
            continue
        seen.add((t, url))
        out.append({"text": t, "source_url": url, "earlier_verdict": verdict})
    return out


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def false_variant(text_: str) -> str | None:
    m = _NUM.search(text_)
    if not m:
        return None
    num = m.group(0)
    last = len(num) - 1 - next(i for i, ch in enumerate(reversed(num)) if ch.isdigit())
    changed = num[:last] + str((int(num[last]) + 1) % 10) + num[last + 1:]
    return text_[:m.start()] + changed + text_[m.end():]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    ap.add_argument("--max-claims-per-request", type=int, default=40)
    args = ap.parse_args()
    load_env()
    url = (args.url or os.environ.get("VERIFIER_URL") or "http://127.0.0.1:8002").rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['VERIFIER_TOKEN']}"}

    originals = load_claims()
    items = []
    for i, c in enumerate(originals):
        items.append({**c, "claim_id": f"o{i}", "kind": "original", "expected": None})
        fv = false_variant(c["text"])
        if fv:
            items.append({"text": fv, "source_url": c["source_url"], "claim_id": f"f{i}", "kind": "false_variant",
                          "expected": "unsupported", "of": f"o{i}"})

    by_url: OrderedDict[str, list] = OrderedDict()
    for it in items:
        by_url.setdefault(it["source_url"], []).append(it)
    batches, cur = [], []
    for group in by_url.values():
        if cur and len(cur) + len(group) > args.max_claims_per_request:
            batches.append(cur)
            cur = []
        cur += group
    if cur:
        batches.append(cur)

    print(f"{len(originals)} original claims, {sum(i['kind'] == 'false_variant' for i in items)} false variants, "
          f"{len(by_url)} URLs, {len(batches)} requests -> {url}")
    verdicts, requests_log = {}, []
    with httpx.Client(timeout=httpx.Timeout(900, connect=30)) as client:
        for n, batch in enumerate(batches, 1):
            env = {"request_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()), "time_budget_ms": 600000,
                   "budget": {"max_steps": 40, "max_fetches": 60, "max_tokens": 600000},
                   "payload": {"claims": [{"claim_id": it["claim_id"], "text": it["text"],
                                           "source_url": it["source_url"]} for it in batch]}}
            t0 = time.monotonic()
            r = client.post(url + "/verify", json=env, headers=headers)
            body = r.json()
            for v in (body.get("result") or {}).get("verdicts", []):
                verdicts[v["claim_id"]] = v["verdict"]
            requests_log.append({"request": n, "claims": len(batch), "http_status": r.status_code,
                                 "status": body.get("status"), "usage": body.get("usage"), "error": body.get("error"),
                                 "latency_ms": int((time.monotonic() - t0) * 1000), "run_id": env["run_id"]})
            print(f"  request {n}/{len(batches)}: HTTP {r.status_code} {body.get('status')} {body.get('usage')} "
                  f"{(body.get('error') or {}).get('code') or ''}")

    for it in items:
        it["verdict"] = verdicts.get(it["claim_id"])        # None = not reached (partial)

    fv = [i for i in items if i["kind"] == "false_variant"]
    fv_judged = [i for i in fv if i["verdict"] in ("supported", "unsupported")]
    fv_accepted = [i for i in fv_judged if i["verdict"] == "supported"]
    orig = [i for i in items if i["kind"] == "original"]
    def count(lst, v):
        return sum(1 for i in lst if i["verdict"] == v)
    agree = [i for i in orig if i["verdict"] is not None and i["verdict"] == i["earlier_verdict"]]
    summary = {
        "false_variants": {"total": len(fv), "judged": len(fv_judged), "wrongly_supported": len(fv_accepted),
                           "unsupported": count(fv, "unsupported"), "unreachable": count(fv, "unreachable"),
                           "not_reached": count(fv, None)},
        "originals": {"total": len(orig), "supported": count(orig, "supported"),
                      "unsupported": count(orig, "unsupported"), "unreachable": count(orig, "unreachable"),
                      "not_reached": count(orig, None), "agree_with_earlier_verdict": len(agree)},
    }
    print(json.dumps(summary, indent=1))
    for i in fv_accepted:
        print("  FALSE VARIANT ACCEPTED:", i["text"][:120], "|", i["source_url"])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = ROOT / "traces" / "a2-tests" / f"verifier-eval-{stamp}.json"
    out.write_text(json.dumps({"verifier_url": url, "at": stamp, "summary": summary, "requests": requests_log,
                               "items": items}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"-> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
