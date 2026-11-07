"""
Fault-injection proxy between the orchestrator and the REAL researcher: it plays a researcher that
cites unsafe URLs. Every /research request is forwarded unchanged (same body, same Authorization
header) to the real researcher; on a 200 answer with developments, two clearly labelled claims are
appended to the first development:

    ...#fault-metadata   source_url http://169.254.169.254/latest/meta-data/   (cloud metadata address)
    ...#fault-localhost  source_url http://localhost/admin                      (loopback)

Everything after that is the real system: the orchestrator validates the answer, sends all claims to
the verifier, the verifier's guardrails refuse both URLs before any request (-> "unreachable"), and
the run is saved and shown on the Tracker page. The claim texts start with "FAULT INJECTION TEST" so
the run can never be mistaken for real research.

    backend\\.venv\\Scripts\\python.exe -m uvicorn tests.fault_proxy:app --port 8011
    (then start the backend with RESEARCHER_URL=http://127.0.0.1:8011)

Env: FAULT_PROXY_TARGET (default http://127.0.0.1:8001).
"""
import json
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

TARGET = os.environ.get("FAULT_PROXY_TARGET", "http://127.0.0.1:8001").rstrip("/")
INJECTED = [
    ("fault-metadata", "FAULT INJECTION TEST: this claim was added by tests/fault_proxy.py and cites the cloud "
                       "metadata address, which the verifier must refuse without fetching.",
     "http://169.254.169.254/latest/meta-data/"),
    ("fault-localhost", "FAULT INJECTION TEST: this claim was added by tests/fault_proxy.py and cites a loopback "
                        "URL, which the verifier must refuse without fetching.",
     "http://localhost/admin"),
]

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/research")
async def research(request: Request):
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "content-type")}
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15)) as client:
        resp = await client.post(f"{TARGET}/research", content=await request.body(), headers=headers)
    passthrough = {k: v for k, v in resp.headers.items() if k.lower() == "retry-after"}
    try:
        body = resp.json()
    except ValueError:
        return Response(resp.content, status_code=resp.status_code, headers=passthrough)
    devs = ((body.get("result") or {}).get("developments")) if resp.status_code == 200 else None
    if devs:
        first = devs[0]
        room = 10 - len(first["claims"])
        first["claims"] += [{"claim_id": f"{first['id']}#{cid}", "text": text, "source_url": url}
                            for cid, text, url in INJECTED[:max(0, room)]]
        print(json.dumps({"fault_proxy": "injected", "development": first["id"],
                          "claims": [c for c, _, _ in INJECTED[:max(0, room)]]}), flush=True)
    return JSONResponse(body, status_code=resp.status_code, headers=passthrough)
