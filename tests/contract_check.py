"""
Offline checks for the shared wire contract (shared/contract.py): strict request parsing, the
canonical body hash used for idempotency, and response rules.

Usage: backend\.venv\Scripts\python.exe tests\contract_check.py
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from shared.contract import Envelope, VerdictOut, WorkerResponse, body_hash  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    passed, failed = (passed + 1, failed) if cond else (passed, failed + 1)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"   {detail}"))


def invalid(model, data):
    try:
        model.model_validate(data)
        return False
    except ValidationError:
        return True


env = {"request_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()), "time_budget_ms": 60000,
       "budget": {"max_steps": 10, "max_fetches": 8, "max_tokens": 40000}, "payload": {}}
check("PDF example envelope parses", Envelope.model_validate(env).time_budget_ms == 60000)
check("canonical hash ignores key order and whitespace",
      body_hash(env) == body_hash(dict(reversed(list(env.items())))))
check("canonical hash: nested key order ignored too",
      body_hash({**env, "budget": {"max_tokens": 40000, "max_fetches": 8, "max_steps": 10}}) == body_hash(env))
check("canonical hash: a changed time budget is a different body",
      body_hash({**env, "time_budget_ms": 59999}) != body_hash(env))
check("strings that look like numbers are refused", invalid(Envelope, {**env, "time_budget_ms": "60000"}))
check("booleans are not integers", invalid(Envelope, {**env, "time_budget_ms": True}))
check("unknown top-level field refused", invalid(Envelope, {**env, "reasoning": "x"}))
check("unknown budget field refused", invalid(Envelope, {**env, "budget": {**env["budget"], "max_searches": 1}}))
check("evidence required on supported", invalid(VerdictOut, {"claim_id": "a", "verdict": "supported", "evidence": None}))
check("evidence null otherwise", invalid(VerdictOut, {"claim_id": "a", "verdict": "unreachable", "evidence": "x"}))
check("evidence <= 300 chars", invalid(VerdictOut, {"claim_id": "a", "verdict": "supported", "evidence": "x" * 301}))
check("'unverified' is not a worker verdict", invalid(VerdictOut, {"claim_id": "a", "verdict": "unverified",
                                                                   "evidence": None}))
base = {"request_id": env["request_id"], "run_id": env["run_id"], "result": {}, "usage": {"steps": 0, "fetches": 0,
        "tokens": 0, "searches": 0}}
check("partial without error refused", invalid(WorkerResponse, {**base, "status": "partial", "error": None}))
check("complete with error refused", invalid(WorkerResponse, {**base, "status": "complete",
                                                              "error": {"code": "x", "message": "m", "retryable": False}}))
check("failed with a result refused", invalid(WorkerResponse, {**base, "status": "failed",
                                                               "error": {"code": "x", "message": "m", "retryable": False}}))
print("\ntrace log: URLs are redacted before they are written")
import json as _json  # noqa: E402
import tempfile  # noqa: E402

from shared.tracelog import TraceLog, redact_url  # noqa: E402

check("query values replaced, names kept", redact_url("https://x.org/p?token=abc123&page=2") ==
      "https://x.org/p?token=REDACTED&page=REDACTED", redact_url("https://x.org/p?token=abc123&page=2"))
check("user:password@ removed", redact_url("https://bob:hunter2@x.org/a") == "https://x.org/a")
check("fragment dropped, path kept", redact_url("https://x.org/a/b#access_token=zzz") == "https://x.org/a/b")
check("URL without query unchanged", redact_url("https://example.com/news/qwen-4") == "https://example.com/news/qwen-4")
path = tempfile.mktemp(suffix=".jsonl")
TraceLog("verifier", path).log(run_id=None, request_id=None, step=1, direction="out", endpoint_or_tool="fetch_article",
                               status="failed", url="https://cdn.example/f?sig=SECRET-SIG-1&key=SECRET-KEY-2",
                               detail="redirect to https://u:SECRET-PW-3@evil.example/x?t=SECRET-T-4 refused")
raw = open(path, encoding="utf-8").read()
check("no query value, password or signature from a URL reaches the trace file (url field AND free text)",
      not any(s in raw for s in ("SECRET-SIG-1", "SECRET-KEY-2", "SECRET-PW-3", "SECRET-T-4"))
      and _json.loads(raw)["url"] == "https://cdn.example/f?sig=REDACTED&key=REDACTED", raw)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
