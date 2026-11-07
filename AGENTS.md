# Codex Engineering Instructions — FNMS Assignment 2: Agentic Orchestration

> **Purpose:** Implement NYU CSCI-GA.2630, Fall 2026, **Assignment 2: Agentic Orchestration** on top of the existing Assignment 1/1B codebase.
>
> **Repository:** https://github.com/HanquanL/fnms-assignment-1b (default branch `main`)
>
> **Source of truth:** The instructor's seven-page PDF, `FNMS Fall 2026 Assignments 2.pdf`. If it is available locally, read it in full before coding. This document operationalizes that PDF; it does not replace it. When a detail here conflicts with the PDF, follow the PDF and flag the discrepancy. The PDF refers to the previous tracker as “A2”; in this repository, the previous working tracker is under `tracker/` from Assignment 1B.

## 0. Your role and working agreement

You are Codex, acting as a **careful implementation partner and systems engineer**, not an autonomous product designer. The student owns architectural decisions and the final submission.

1. **First inspect, then plan, then code.** Read `README.md`, `AGENT.md`, `config.yaml`, relevant source files, and the assignment PDF. Run the baseline tests before changing behavior. Report existing failures honestly; do not claim tests passed unless executed.
2. **Preserve the working A1/A1B application.** Extend the existing tracker, API, data models, UI, and test style. Do not rewrite the project, change the database vendor, switch languages, or introduce unnecessary packages.
3. **Work in small, reviewable phases.** Before each major phase, state: objective, files likely to change, success criteria, risks, and test commands. Give a concise explanation of important design decisions; ask before destructive migrations, costly live API runs, deployment, or external pushes. For normal local edits and offline tests, proceed without repeated permission requests.
4. **No fabricated results.** Do not invent trace latencies, verifier accuracy, test results, cost estimates, deployed URLs, or hand-checked claims. Mark unfinished empirical sections as TODO until measurements exist.
5. **No framework shortcuts.** Implement orchestration, routing, budgets, and retry logic as ordinary code. **Do not use LangGraph, CrewAI, AutoGen, OpenAI Agents SDK handoffs, OpenClaw, or similar agent/multi-agent orchestration frameworks.** Existing model SDKs, FastAPI, SQLAlchemy, Pydantic, `httpx`, and other normal HTTP libraries are allowed.
6. **Never leak secrets.** Do not print, log, paste, or commit real API keys, service tokens, database credentials, JWT secrets, or authorization headers. Inspect existing `.env` files cautiously without repeating values. The current public repository includes tracked `backend/.env` and `frontend/.env` files (the README calls them course-only); treat any usable credential as sensitive and flag for rotation/cleanup, rather than spreading it. Use `.env.example` files with blank/sample placeholders.
7. **Do not alter history or production data.** Never force-push, reset a shared database, delete user data, or overwrite existing reports/traces without explicit authorization. Use a feature branch if appropriate.
8. **Be honest about academic ownership.** Explain nontrivial behavior so the student can defend every part of the system at the demo. Favor readable functions and explicit tests over clever abstractions.

## 1. Existing repository — verified starting point

This map reflects the repository inspected on 2026-11-04; re-check it in the student's local checkout because it may have changed.

| Existing file(s) | Reuse or extend for Assignment 2 |
|---|---|
| `tracker/agent.py` | Handwritten researcher agent loop; adapt to worker request budgets and an HTTP service wrapper. |
| `tracker/llm/`, `tracker/search.py`, `tracker/tools.py` | Gemini and Tavily integrations; retain rate-limit handling and provider abstraction. |
| `tracker/fetch.py`, `tracker/urlguard.py`, `tracker/extract.py` | Strong existing fetch defenses (DNS/IP vetting, pinned connections, manual redirect inspection, timeout and size caps); reuse the mechanism in the verifier and test redirect accounting. |
| `tracker/report.py` | Existing claim/evidence validation; useful for researcher output, but **not a replacement for independent verification**. |
| `tracker/budget.py`, `tracker/errors.py` | Budget and retry primitives; adapt to the new **wire budget** and accurately account for actual calls and redirect hops. |
| `tracker/trace.py` | Existing JSONL logger; extend to Assignment 2's required cross-service trace fields. |
| `tracker/memory.py`, `tracker/backend.py`, `tracker/__main__.py` | Prior CLI, memory classification, and database API client; **do not let the researcher worker read/write persistence** in A2. Move run-level memory and reporting responsibility to the orchestrator. |
| `backend/app/main.py`, `backend/app/routers/tracker.py` | FastAPI application and existing A1 tracker endpoints. A natural place for the orchestrator-facing API and sole DB ownership. |
| `backend/app/models.py`, `backend/app/tracker_schemas.py` | SQLAlchemy models and Pydantic DTOs; extend for per-run claims and verdicts. Note: `tracker_schemas.py` currently defines `RunIn` twice; inspect/fix carefully if editing it. |
| `frontend/src/pages/TrackerPage.jsx` | Existing safe React text-rendered tracker view and run history; add per-claim badges/evidence and worker-failure status. |
| `config.yaml`, `.env.example`, `backend/.env.example` | Policy, total run budgets, nonsecret configuration, and documented secret names. |
| `tests/*.py`, `reports/run*.md`, `traces/run*.jsonl`, `AGENT.md` | Existing regression tests and A1B baseline measurements; preserve for comparison. |

**Existing stack:** Python/FastAPI, SQLAlchemy/PostgreSQL on **Neon** (not Supabase), React/Vite, Gemini, Tavily, `httpx`. The PDF mentions Supabase pause behavior, but the assignment requires an awake A1 database, **not a mandatory migration to Supabase**. Keep Neon; verify its grading-time availability.

**Baseline first:** Follow `README.md` from a clean clone. Run the existing offline tests and backend-dependent tests where the DB is available; record the commands/results. Do not consume model/search quotas merely to establish the baseline without student approval. Preserve the previous single-agent tracker as a measurable comparison and, if practical, keep its CLI running.

## 2. Target architecture (nonnegotiable)

Build **three logically separate HTTP services** (three processes, each on its own port/host):

```text
React A1 tracker UI
       |
       v
Orchestrator (existing FastAPI backend, e.g. localhost:8000)
  |    - starts run, creates UUIDs, routes requests
  |    - owns config total budget and apportions worker shares
  |    - enforces retries, validates responses, writes final report
  |    - ONLY service that accesses A1 database/run history/last top K
  |
  +--> HTTP POST /research --> Researcher (e.g. localhost:8001)
  |                             - existing tracker loop wrapped as a service
  |                             - topic, K -> ranked developments with claims
  |                             - no DB access; no state shared with others
  |
  +--> HTTP POST /verify ----> Verifier (e.g. localhost:8002;
                                deployed to Render free tier)
                                - receives only claim text and cited URL
                                - fetches cited pages independently
                                - returns supported/unsupported/unreachable
                                - no DB access, no researcher reasoning
```

- All worker communications **only over HTTP**. No direct worker function calls/imports between services, shared mutable memory, or worker-accessible database tables. Sharing **pure schemas/data contracts** between services is allowed.
- Prefer a **deterministic, code-driven orchestrator** (not an LLM). If an LLM orchestrator is proposed, obtain approval and justify in `ORCHESTRATION.md` what it decides that code cannot.
- Reuse the existing backend as the orchestrator process (the React development server is a UI, not one of the three worker services). A new authenticated endpoint for starting runs is fine; document its behavior. The assignment only prescribes the workers' HTTP paths.
- Researcher and verifier require different service bearer tokens. The orchestrator keeps the tokens server-side. Never send them to the browser.
- The independent verifier must **not** receive researcher summaries, reasoning, article text, screenshots, source extracts, or tool histories. The verifier receives only the required envelope and `claim_id`, `text`, `source_url` for each claim.

## 3. Exact worker request and response contract

For **every POST** to either worker, the orchestrator sends a JSON envelope:

```json
{
  "request_id": "<uuid generated once per logical request>",
  "run_id": "<uuid shared across this run>",
  "time_budget_ms": 60000,
  "budget": { "max_steps": 10, "max_fetches": 8, "max_tokens": 40000 },
  "payload": {}
}
```

Here **one step = one model call**, and **one fetch = one HTTP request to a web page, including each redirect hop**. Count real operations, not just successful ones. If a model/provider retries, count its actual billable usage and actual calls; document unavoidable unknown usage on failures. Separate search-provider API calls from page fetches, while still tracking and limiting search usage/cost under existing policy.

Required worker endpoints:

| Method | Endpoint | `payload` | Successful result body |
|---|---|---|---|
| GET | `/healthz` | none; **no auth** | `{"status":"ok"}` |
| POST | `/research` | `{"topic":"...","k":5}` | `{"developments":[{"id":"...","title":"...","summary":"...","claims":[{"claim_id":"...","text":"...","source_url":"https://..."}]}]}` |
| POST | `/verify` | `{"claims":[{"claim_id":"...","text":"...","source_url":"https://..."}]}` | `{"verdicts":[{"claim_id":"...","verdict":"supported|unsupported|unreachable","evidence":null}]}` |

- Developments must be **ranked** (array order defines ranking), unique and capped by K; use stable development IDs where feasible (existing development `key` can serve as `id`). Preserve exactly one cited URL per atomic claim.
- Verdict `evidence` is a **string only when supported**; it is `null` for unsupported and unreachable. The orchestrator uses `unverified` **only when verifier results are missing/invalid/unavailable**, not as a fourth verdict returned by the verifier.
- The PDF also requires worker **status, partial results, usage reporting and structured errors**, but does not fully spell out all success-response fields in its sample table. Define and document a consistent additional envelope, e.g. `status: complete|partial|failed`, `usage: {steps,fetches,tokens}`, optional `error: {code,message,retryable}` on partial/failure. Use the same shape for both workers; do not pretend an unspecified field name is mandated by the PDF. Keep all required payload/result fields above.
- The orchestrator MUST validate both worker response schemas and semantics: UUIDs, statuses, allowed values, field sizes, unique IDs, per-worker budgets, and, especially, **each verifier `claim_id` must have been sent in that request**. Reject extra/duplicate IDs; distinguish missing IDs allowed in a declared partial response from invalid extra IDs. A malformed worker response is a worker failure: discard untrusted content, mark the run partial, do not crash or contaminate the report.

### HTTP status behavior

| Status | Meaning | Retry? |
|---|---|---|
| `200` | Complete **or partial** result | No |
| `400` | Request schema invalid | Never |
| `401` | Missing/wrong worker token | Never |
| `409` | Same `request_id`, different parsed JSON request body | Never |
| `500` | Terminal worker failure, such as invalid model key or exhausted daily quota; `status: failed`, `retryable: false` | Never |
| `503` | Worker temporarily overloaded/rate-limited or same ID still in flight; include `Retry-After` | Yes |

**Security ordering:** Authenticate `Authorization: Bearer <RESEARCHER_TOKEN|VERIFIER_TOKEN>` **before** revealing request schema errors. Do not leak validation details to unauthenticated callers. FastAPI defaults to `422` on invalid Pydantic bodies; implement explicit worker-level error handling so these endpoints yield the required `400`, after token verification.

## 4. Behavior that must survive real networks

### 4.1 Deadlines, cold starts, partial runs

- Start each worker's deadline using a **monotonic clock when that worker receives the request**, not when the orchestrator sends it. Stop work once `time_budget_ms` or a step/fetch/token share is exhausted; return `200` with `status: partial`, already completed results, and a meaningful nonretryable `error` (e.g. `time_budget_exhausted`).
- The orchestrator HTTP timeout **must exceed** the worker's stated time budget **plus cold-start overhead**. Render's free verifier may sleep after ~15 minutes idle and take roughly a minute to wake. Use configurable connection/response/overall timeouts with sensible slack; justify actual numbers with measured traces, not guesses. Avoid an infinite retry loop.
- A worker failure/timeout never prevents report creation or DB persistence. Mark report **partial** and name the worker that failed.
- If **verifier unavailable**, display the research claims as **unverified** (not supported).
- If **researcher unavailable**, state “no new results” and show the last successful run's top K as **stale**, clearly separated from fresh findings. Do not silently relabel stale data as fresh.
- Ensure `unreachable` for independently inaccessible URLs is distinct from `unsupported` for fetched sources that do not support the claim.

### 4.2 Idempotency and retries

- Orchestrator retries **only** transient connection failures, timeouts and `503`. Use capped exponential backoff (optionally jitter) and honor `Retry-After` when present. Terminal `400`, `401`, `409`, `500` stop immediately with clear diagnostics.
- **A retry must resend precisely the same logical JSON body and the same `request_id`**. Do not reduce `time_budget_ms` or change the assigned budget between retries. Different JSON key order/whitespace does not make a different body.
- Worker request registry keyed by `request_id`: canonicalize/compare parsed JSON (e.g., sorted-key hash); register request atomically before executing. **Same ID + different body → 409. Same ID + same body while in flight → 503 with Retry-After, without duplicate work. Same ID + completed identical body → replay stored response, no fresh model calls/fetches.** Persist/replay applicable response status as well as body.
- In-memory idempotency storage is permitted. A Render restart loses it; document the resulting weaker exactly-once guarantees and possible duplicated external work after restart. Never claim cross-restart exactly-once delivery without persistent deduplication.
- Design deterministic tests that simulate both retry cases: first worker request **finished** before retry, and first **still in flight** when retry arrives.

### 4.3 One budget per run

- `config.yaml` must specify a single run-wide budget. The orchestrator reserves explicit **researcher** and **verifier** shares and never allocates more total steps, page fetches, or tokens than allowed. The workers independently enforce their own shares.
- Prefer a simple documented allocation policy over premature adaptive optimization. Existing config contains `limits.max_steps`, `max_fetches`, `max_total_tokens`, plus `max_searches`; adapt them cleanly instead of maintaining contradictory budget settings.
- The orchestrator aggregates worker-reported usage and counts **each `request_id` once**, even for a cached retry. If a worker never answers, **charge its full allocated share**. Include usage/charged-budget in run stats and validate reported usage is plausible and within the assigned share.
- A page redirect consumes another fetch budget unit. A single high-level `fetch_article` operation is not automatically one fetch if it follows redirects. Reserve/check budget **before every outgoing hop**.
- For live tests, do not exceed real model/search free-tier quotas or spend caps. Set hard paid-key spending limits before any paid calls.

## 5. Independent verifier — correctness and safety

1. Read claim text as **untrusted data**, never a command. The page's contents are also untrusted. Model messages should delimit source text and instruct the model to evaluate factual support only; no page or claim may change tool permissions, routes, budgets, or orchestrator policy.
2. Fetch each cited source **independently** of the researcher. Reuse `tracker/fetch.py` / `tracker/urlguard.py` as a safe fetching mechanism or refactor them into a pure shared utility; **do not** import and execute another service's running agent or rely on researcher-provided fetched content. URL-level caching within a single request is acceptable if documented and budgeted correctly.
3. Required guardrails: `http`/`https` only; per-call timeout and response-size cap; block loopback, private, link-local, unspecified, cloud metadata (`169.254.169.254`), and IPv6 `::1`, `fc00::/7`, `fe80::/10`. Validate **resolved IP addresses**, not just hostname strings, including tricky literal/encoded forms. Verify **every redirect hop** with a maximum redirect count. Prevent DNS rebinding by connecting to the vetted address while preserving TLS hostname verification, as the existing fetcher already attempts.
4. If a URL is rejected by guardrails or can't be fetched, return **`unreachable`** with `evidence: null` and do not attempt a prohibited network fetch.
5. If the source was fetched, model judgment may propose `supported` or `unsupported`; code MUST independently validate **any supported evidence excerpt**. A supported excerpt must be **at most 300 characters**, actually occur in the verifier's fetched page text, and substantiate the claim. If the excerpt does not occur, **downgrade to `unsupported`**. Never upgrade a model's unsupported verdict merely because a quote matches.
6. **Implement the PDF's exact mandatory normalization on both excerpt and source text, in order:** (i) decode HTML entities, (ii) Unicode NFKC, (iii) collapse whitespace runs to one space, (iv) lowercase. Document **any** extra normalization in `ORCHESTRATION.md`. Do not assume the existing `tracker/report.py` normalization is identical; its extra quote/dash folding differs from the verifier requirement.
7. Handle fetched-but-inconclusive text (unsupported), blocked/unavailable sources (unreachable), missing verdicts (unverified at orchestrator), and malformed model output safely. Do not return invented evidence.
8. Include test fixtures with explicit prompt-injection attempts embedded in a claim or fetched article. A malicious claim/page must not alter validation, routing, service tokens, budgets, or verdict schema.

## 6. Persistence, UI, and output integrity

- **Only orchestrator/backend** may access the A1 database for run history, last top K, and report state. Researcher/verifier must not import the backend DB client or hold DB credentials.
- Reuse existing models `TrackerRun`, `TrackerDevelopment`, `TrackerRanking` and existing `backend/app/routers/tracker.py` where possible. The database currently stores per-development sources, **not individual claims/verdicts**. Add an appropriately normalized **per-run claim/verdict record** (e.g., a `tracker_claims` table linked to run/development) so each claim's text, URL, verification status, evidence, and provenance survives refresh/restart. Avoid conflating a source's old evidence with the verifier's independent evidence.
- Preserve A1 user ownership/auth rules, existing run history, and prior `new/still/dropped` semantics. For incomplete runs, avoid falsely declaring missing developments dropped. Provide safe DB schema upgrades; SQLAlchemy `create_all` **does not alter existing columns**, so use an explicit migration if existing tables change. Back up/verify before altering real data.
- Show per-claim labels: **supported / unsupported / unreachable / unverified**. Show the verifier evidence only for supported claims. Run history must display complete/partial status and **which worker failed**. Show the stale prior top K when researcher failed.
- Keep all web-derived strings rendered as **text**, not raw HTML. Preserve `TrackerPage.jsx`'s safe React escaping and safe URL handling; no `dangerouslySetInnerHTML` or unsafe Markdown rendering. Be cautious with URLs that the verifier rejected.
- Render the final report from validated researcher/verifier data, never from raw, unsanitized worker text. Save the report even when one worker fails.

## 7. Cross-service JSONL traces

Each of the three services must log **every inbound request and every outbound call** (HTTP, model call, search/fetch/tool call as applicable), one JSON object per line, with these fields present:

```json
{
  "ts": "2026-11-04T00:00:00.000Z",
  "service": "orchestrator|researcher|verifier",
  "run_id": "<uuid or null for non-run health checks>",
  "request_id": "<uuid or null where not applicable>",
  "step": 1,
  "direction": "in|out",
  "endpoint_or_tool": "POST /verify",
  "status": "ok|partial|failed|retry|...",
  "latency_ms": 123,
  "attempt": 1,
  "tokens": 0
}
```

- **Measure latency on the caller's own monotonic clock**, not by subtracting timestamps from the laptop and Render (their wall clocks can differ).
- Propagate the same `run_id` and logical `request_id` on a cross-service call. A retry keeps its request ID but increments `attempt`. Distinguish model **token counts** (`tokens` numeric) from secret **authentication tokens** (never log the latter).
- Do not write authorization values, API keys, model secrets, raw credentials, or sensitive request headers to traces. Avoid logging entire untrusted source pages or prompts unnecessarily.
- Verify that traces from the three services can be correlated for the first real orchestrated run. Keep artifacts needed for `ORCHESTRATION.md`.

## 8. Implementation phases and gates

**Phase 0 — Baseline and gap analysis**
- Verify clean clone, current dependencies, current tests, tracker operation (only use a paid/live model with approval), and database reachability.
- Produce a short file-by-file change plan. Distinguish verified behavior from proposed design. Record the A1B baseline from existing reports/traces without fabricating metrics.
- Gate: student can see what will change and what will be preserved.

**Phase 1 — Shared contract and tests**
- Define request/response schemas, error codes, service auth, canonical request hash, budgeting types, and cross-service trace schema in a small pure module (sharing schemas is permitted).
- Write unit tests for malformed input, auth-before-validation, response validation, and usage semantics.
- Gate: contract tests pass without any live model calls.

**Phase 2 — Researcher HTTP service**
- Wrap the working tracker loop behind `POST /research`, plus unauthenticated `GET /healthz`.
- Adapt the current report's `{key,rank,claims:[{text,url,evidence}]}` into ordered `{id,title,summary,claims:[{claim_id,text,source_url}]}` without losing provenance. The researcher may use its *own* fetched sources; it does **not** send reasoning or fetched pages to verifier.
- Disable the old researcher's A1 DB reads/writes in worker mode, implement per-request budget/deadline, usage, idempotency, and JSONL tracing.
- Gate: researcher contract/idempotency/partial tests pass; old tracker tests still pass.

**Phase 3 — Verifier HTTP service**
- Implement strict `POST /verify` and `GET /healthz`, separate token, independent fetch, DNS/redirect guardrails, normalized evidence checks, and injectable model/fetch fixtures for offline tests.
- Gate: verifier passes malicious/blocked URL, evidence, tiny-deadline, auth, same-ID, and in-flight duplicate tests **without** network access to prohibited addresses.

**Phase 4 — Orchestrator in backend**
- Add an authenticated run-start API; generate run UUID and per-worker request UUIDs, allocate budgets, call workers over HTTP, validate responses, retry transient errors only, and persist an honest complete/partial report.
- Handle missing/malformed verifier, missing researcher, stale last top K, and charge-on-no-answer. Ensure no direct imports/calls into live worker logic and no worker DB access.
- Gate: simulated complete run and both failure branches pass end-to-end, including persisted reports.

**Phase 5 — Database, frontend, and deployment**
- Add claim/verdict persistence and API DTOs without breaking user ownership or old data; update `TrackerPage.jsx` and run history.
- Deploy **verifier to Render's free tier**, set environment secrets in Render, check `/healthz`, and exercise a genuinely cold request. Researcher/orchestrator may run locally unless instructor directions elsewhere require deployment.
- Gate: the UI visibly displays all four claim labels, evidence/stale indicators, and failed-worker status after refresh; Render auth works; database remains available.

**Phase 6 — Fault-injection, measured run, documentation**
- Run all rubric tests below on **both workers**, capture correlatable traces and actual latency/cost counts, compare with A1B baseline, hand-check verdicts, write `ORCHESTRATION.md`.
- Gate: full regression suite passes, no undisclosed live quota issues, no fabricated benchmark results, demo checklist complete.

## 9. Required test matrix (apply to EACH worker)

| Input/scenario | Expected result |
|---|---|
| Missing token | `401` |
| Wrong token | `401` |
| Malformed JSON/schema with valid token | `400` |
| Missing token **and** malformed body | `401` (auth first) |
| Same `request_id`, semantically same JSON twice | Same stored response; **zero extra model calls and page fetches** |
| Same ID with changed body (including changed budget/time) | `409` |
| Same ID while first copy is running | `503` plus `Retry-After`; no duplicate execution |
| Claim URL `http://169.254.169.254/` or `http://localhost/` | `unreachable`; **no network fetch attempted** (verifier); researcher rejects unsafe fetch too |
| IPv6 loopback/private/link-local or public-to-private redirect | Rejected before forbidden connection |
| Very small `time_budget_ms` | `200`, `status: partial`, previous completed results retained |
| Worker timeout, refused connection, transient `503` | Capped retry with same request ID/body; `Retry-After` respected |
| Terminal `400/401/409/500` | No retry |
| Invalid response or unknown verifier `claim_id` | Orchestrator marks failure/partial and excludes unsafe result |
| Verifier down | Claims shown as `unverified`; report saved partial |
| Researcher down | No new results; last top K shown as **stale**; report saved partial |
| Retry after completed request, retry during in-flight request | No duplicate external calls inside same process lifetime |
| Worker never answers | Orchestrator charges full allocated budget share |
| Evidence excerpt missing from fetched source | `unsupported`, even if model said supported |
| HTML entity + NFKC + whitespace + lowercase variations | Normalized excerpt test behaves exactly as specified |
| Prompt-injection text in source/claim | No change to routing, instructions, budget, or service permissions |

Prefer small, deterministic, dependency-injected tests using `httpx.MockTransport` or local test servers. The existing repository uses script-style tests under `tests/`; keep those working, and add focused new tests in the repository's style. **Do not run uncontrolled live model calls in CI.** For Render latency tests, record actual wall-clock/caller-side timings and mark cold/warm runs distinctly.

## 10. `ORCHESTRATION.md` — five mandatory questions

Answer **all five** prompts from assignment PDF page 7, with code references and empirical evidence:

1. **Contract:** Why these fields? Identify one item deliberately hidden from verifier (such as the researcher's reasoning/article text) and explain the failure mode if sent.
2. **Network:** From run 1 traces, list **every cross-service call with caller-measured latency**; explain time breakdown, measured Render cold-start cost, and why timeouts were chosen.
3. **Duplicates:** When orchestrator times out and retries the verifier, explain behavior if original finished vs still running, quoting **actual code**. Explain what changes after a Render restart (lost in-memory idempotency state).
4. **Injection across agents:** Show how a malicious page could try to alter a verifier verdict, what defenses work, and what residual vulnerabilities remain.
5. **Was second agent worth it?:** Compare actual run cost (or token/API-call proxy on free tiers) and latency against previous single-agent tracker. Count **unsupported** and **unreachable** verifier verdicts. **Manually review every unsupported claim plus 10 randomly chosen supported claims**; report number of wrong verdicts, selection method/seed, and denominators. If fewer than 10 supported claims exist, explain the shortfall and report what was checked. Never manufacture results.

Additional documentation: update README with startup commands for three processes, configuration, secrets, Render deployment, test commands, and known limitations. Include `.env.example` entries for `RESEARCHER_TOKEN`, `VERIFIER_TOKEN`, researcher/verifier URLs, model/search keys, database URL, and any deployment-specific environment variables. Document which variables belong to which service, and preserve the existing A1 authentication behavior.

## 11. Definition of done — final review

Before declaring completion, report a concise checklist and evidence for every line:

- [ ] Three independent processes communicate only over HTTP; both workers have `/healthz`.
- [ ] No prohibited agent framework; orchestrator is ordinary code.
- [ ] Exact input envelope and `/research`/`/verify` payload contracts supported.
- [ ] Separate bearer tokens, `401` before `400`; no secrets in repo/logs.
- [ ] Worker schema validation, response validation, `400/401/409/500/503` behavior correct.
- [ ] Workers enforce time/step/fetch/token limits; partial output works.
- [ ] Client timeout accommodates worker budget and Render cold start.
- [ ] Retries are bounded and identical; idempotency handles complete/in-flight/conflict cases.
- [ ] Single run-wide budget allocation/accounting, including unknown failed-worker charge.
- [ ] Independent verifier fetch, DNS/IPv4/IPv6/redirect SSRF guardrails.
- [ ] Supported evidence <=300 characters, normalization exact, excerpt checked by code.
- [ ] Untrusted claims/pages cannot act as instructions.
- [ ] Only orchestrator owns DB state; individual claim verdicts persisted.
- [ ] UI shows all four claim labels, excerpts, partial failures, and stale fallback safely.
- [ ] Every service emits required JSONL tracing fields; cross-service timing is caller-side.
- [ ] Render verifier deployed/configured and tested after cold start.
- [ ] Existing A1/A1B tests and new tests pass; baseline still reproducible.
- [ ] `ORCHESTRATION.md` answers all five questions using real runs and hand-checks.

## 12. First message to the student

**Do not begin with a rewrite.** Start by presenting:

1. A brief **current-state summary** of the repository and which existing modules can be reused.
2. A **requirement-to-file gap analysis**, identifying the highest-risk gaps (HTTP isolation, DB ownership, idempotency, budgets, SSRF on redirects, Render timeout, evidence validation, claim persistence).
3. A **minimal proposed directory/endpoint design**, including how the existing FastAPI backend becomes orchestrator without direct calls into the worker implementations.
4. An ordered **implementation plan with test gates**, risks, and decisions requiring student approval.

After the plan is reviewed, implement each phase, run the tests, show diffs/results, and advance. Never mark a requirement completed solely because code was written; show a test or measured demonstration.
