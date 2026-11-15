# Assignment 2 — decisions and debugging notes

I used this file to keep track of decisions that were not obvious from the code, plus the problems I found while testing. The assignment PDF (`FNMS Fall 2026 Assignments 2.pdf`) sets the requirements; these notes explain how I applied them. I kept failed runs and earlier results instead of replacing them with cleaner numbers after each fix.

## Baseline (before any change), 2026-11-04

| Check | Result |
|---|---|
| `tests/guard_check.py` | 34 passed, **1 failed**: "redirect to loopback is rejected at the redirect hop" — the test depends on a live public redirect service, which answered `HTTP 403`. Not a code regression; network-dependent. |
| `tests/agent_check.py` | 47 passed, 0 failed |
| `tests/failure_check.py` | 29 passed, 0 failed |
| `tests/memory_check.py` | 11 passed, 0 failed |
| Neon database | reachable; 3 runs in `tracker_runs`; 6 tables |
| A1B single-agent baseline (from committed traces) | run1: 16 model calls, 7 searches, 6 fetches, 165,064 tokens, 68.4 s. run2: 16 / 7 / 2 fetches, 181,553 tokens, 68.6 s |

My unfinished `run-button` work (`tracker/__main__.py`, `tracker/backend.py`: a `TRACKER_TOKEN` login
path) was kept out of the first A2 commits.

## Choices the PDF left open

| # | Question | PDF | What I did |
|---|---|---|---|
| C2 | Which "last top K" to show when the researcher fails | §6 "shows **last run's** top K, marked stale" | If the previous run was itself a researcher failure it has no top K of its own, so "last run's top K" would be empty. I show the most recent run that has a ranked top K. When the previous run succeeded (the normal case) the two readings agree. |
| C3 | How much of "supported" code can check | §9: code checks that the excerpt (≤300 chars) appears in the fetched text | Whether an excerpt *substantiates* a claim is a semantic judgment that code can't make. Code enforces what the PDF requires (length, presence after normalization) plus a minimum excerpt length (stated in ORCHESTRATION.md as an extra rule, not a normalization); the model is asked to pick an excerpt that substantiates the claim. |
| C4 | The metadata-URL test for the researcher | p.7 lists `http://169.254.169.254/` as a claim test, to be sent "to each worker" | The researcher's payload has no URLs (`{topic, k}`), so this test can't be sent to it as a request. The researcher reuses `tracker/urlguard.py`, covered by `tests/guard_check.py`; the verifier test sends the URL in a claim. |
| C5 | Response envelope | p.3–4: "result out" column; "On partial or failed, `error` says why"; 500 → `status: failed, retryable: false` | `{request_id, run_id, status, result, usage, error}`; `result` holds exactly the PDF's "result out" object. `status` and `error.code/message/retryable` come from the PDF; `usage` and the `result` wrapper are my choice (the PDF asks workers to report usage but doesn't name the field). |
| C6 | Search budget | p.3 envelope budget has exactly `max_steps`, `max_fetches`, `max_tokens` | The wire budget keeps exactly those three keys. Search calls are capped by the researcher's own policy (`researcher.max_searches` in `config.yaml`) and reported in `usage.searches` for cost accounting. |
| C7 | Repository | — | A2 lives in its own repository, `fnms-assignment-2`. Before that repository existed I had pushed an early `a2-orchestration` branch (commits `7a0a722`, `496ca88`) to `fnms-assignment-1b`; it is left there unchanged. |
| C8 | Where the verifier runs | p.2 "Deploy your verifier on Render's free tier" | On 2026-11-04 everything ran locally; on **2026-11-07 I deployed the verifier to Render's free tier**: `https://fnms-verifier.onrender.com`, built from `HanquanL/fnms-assignment-2`, branch `main`. The orchestrator and researcher run locally (allowed by the PDF). Runs 1–3 and the 2026-11-04 fault runs used a local verifier and are kept as measured; the Render run is reported separately in ORCHESTRATION.md. |

## Design decisions

These were the main decisions I made while separating the A1B code into three services. The IDs are used in `ORCHESTRATION.md` and later debugging notes, so I kept them stable.

| # | What I chose | Reason |
|---|---|---|
| D1 | I put the orchestrator in the existing FastAPI backend (`backend/app/orchestrator/`) and wrote the routing in Python, not another LLM. | The order is already known: research, verify, then report. Letting a model decide that order would add cost and another failure mode. |
| D2 | `shared/` holds the wire contract (Pydantic models, error codes, canonical JSON hash) and two stateless helpers (JSONL trace writer, worker HTTP plumbing: auth, 400 mapping, idempotency registry class). No service imports another service's package. | PDF §2 forbids importing *another service's code to call it*; it allows a shared schema file. The helpers hold no state shared between processes: each process creates its own registry and trace file. Sharing them avoids three copies of the same 400/401/409/503 logic. |
| D3 | I kept the verifier's URL guardrails in `verifier/fetchguard.py`, based on the existing tracker URL, fetch and extraction code. | The verifier runs separately on Render. Importing `tracker/` would make that deployment depend on the researcher's package. |
| D4 | Every redirect hop is one fetch. `fetch_article` gained an `on_hop` callback that is called **before** each outgoing request and may refuse it (budget). The researcher's worker budget counts hops, not `fetch_article` calls. | PDF §3: "A fetch is one HTTP request to a web page, redirects included." |
| D5 | I used one Gemini key and ran research before verification rather than running the model calls concurrently. | Both workers would otherwise compete for the free-tier rate limit. Each worker also paces its own calls. |
| D6 | I group claims by source URL, fetch each distinct page once per request, and check the claims for that page in one model call. | Several claims often cite the same article. Re-fetching it for each one would waste time and fetch budget. The cache does not persist across requests. |
| D7 | Verifier model output is JSON (Gemini `responseMimeType: application/json`); code validates every field, ignores claim ids it didn't ask about, downgrades `supported` when the excerpt check fails, and never upgrades. | PDF §9 and §11. |
| D8 | The researcher worker runs with **no memory** (no DB, no seen-URL list). The orchestrator does key dedupe against known developments and new/still/dropped classification (logic re-implemented in `backend/app/orchestrator/memory.py`). | PDF: only the orchestrator reads or writes state. The A1B tracker's memory came from the database. |
| D9 | No `ALTER TABLE` on existing tables. New table `tracker_claims` (created by `create_all`) stores each claim, its URL, its verdict and the verifier's evidence. Which worker failed and the stale-fallback run id are stored in the run's existing `stats` JSON and exposed as explicit API fields. | `create_all` creates new tables but never alters existing ones; avoiding a migration on the shared Neon database removes the riskiest step. |
| D10 | Researcher evidence quotes are **not** on the wire (contract has `claim_id, text, source_url` only). `tracker_sources.evidence` is left `NULL` for A2 runs; the only evidence shown is the verifier's, from `tracker_claims`. | Keeps the researcher's and the verifier's evidence from being confused. |
| D11 | Runs are started from the web page (`POST /api/tracker/orchestrate`, user JWT) or the CLI (`backend/run_orchestrated.py`, which calls the same endpoint), run in a background thread, and use the orchestrator `run_id` as the `tracker_runs.id`. | One id ties the DB row to all three trace logs. |
| D12 | I send a background `GET /healthz` to the verifier when the run starts, while the researcher is working. | Render's free instance can be asleep. The pre-warm can overlap the researcher's work, though `/verify` still has its own cold-start allowance if the ping fails. |
| D13 | Idempotency registry is in memory (per process), keyed by `request_id`, value = SHA-256 of the canonical JSON body (sorted keys, no whitespace) + state (in-flight / done) + stored status code and body. Only final answers (200, 500) are stored; 503s are not. Bounded: oldest finished entries evicted past 1,000 entries. | PDF §7. A Render restart loses it (discussed in ORCHESTRATION.md Q3). |
| D14 | Worker response envelope: `{request_id, run_id, status, result, usage, error}` (see C5). `usage = {steps, fetches, tokens, searches}`; `searches` is 0 for the verifier. | Same shape for both workers; orchestrator validates it identically. |
| D15 | Orchestrator charging: valid response → the usage it reports (must be ≤ the share it was given, else invalid); no answer / timeout / malformed / 5xx → the **full share**; 400/401 → 0 (the worker rejects these before doing any work). Each `request_id` charged once regardless of attempts. | PDF §8. |
| D16 | Traces: each service appends to its own file, `traces/<service>.jsonl` (verifier on Render: also stdout, plus an authenticated `GET /trace?run_id=` to download its log). Required fields always present; extra fields allowed. Inbound requests are logged once when answered, with server-side handling time; caller-side latency is logged by the caller on its own monotonic clock. | PDF §13. |
| D17 | `config.yaml` keeps the A1B `limits:` block next to the new `run_budget`/`allocation`. | `limits` is only read by the single-agent CLI (`python -m tracker run`), kept runnable as the baseline for ORCHESTRATION.md Q5; A2 runs use only `run_budget`/`allocation`. Commented in config.yaml. |
| D18 | Worker URLs default to `http://127.0.0.1:800x`, not `localhost`. | Found in run 1's traces: on Windows `localhost` tries `::1` first, uvicorn listens on IPv4 only, and every call paid ~2 s (4 s for a refused connection). Changed after the measured runs; the runs' numbers are reported as measured. |
| D19 | Evidence quotes in the researcher's report are dropped at the wire boundary (`researcher/core.py` `to_wire`). | Contract: the verifier must not see them, and the orchestrator has no use for them (D10). |
| D20 | For ORCHESTRATION.md Q5 I hand-checked all 13 selected verdicts (every unsupported one plus 10 random supported ones, seed 2630) by opening each cited page and searching it for the claim. My notes are in the table's last column. | The table quotes the page text so each verdict can be re-checked quickly. |

## November 4 — baseline runs

I first tested both workers locally: 16/16 verifier checks and 13/13 researcher checks. Runs 1–3 completed; I also recorded one verifier-down and one researcher-down run. The measurements in `ORCHESTRATION.md` come from these saved traces and the later tests noted here.
- Researcher "Test it yourself" results (PDF p.7), 13/13 both times: on 2026-11-04
  ([traces/a2-tests/matrix-researcher-20261104.json](traces/a2-tests/matrix-researcher-20261104.json))
  and again on 2026-11-07 after the review-round fixes
  ([traces/a2-tests/matrix-researcher-20261107.json](traces/a2-tests/matrix-researcher-20261107.json)).
  The verifier's are in `traces/a2-render/`.
- The React page lints and builds (`npm run build`); the API it reads is covered by
  `tests/orchestrator_check.py`. I checked the page visually on 2026-11-07 (below).

## November 7 — testing an unreachable source

I had not yet seen `unreachable` in an ordinary run, so I could not tell whether the UI displayed it correctly. I added
  `tests/fault_proxy.py`, a test-only proxy that forwards `/research` to the real researcher and
  appends two claims labelled "FAULT INJECTION TEST" citing `169.254.169.254` and `localhost`
  (D21: done at the HTTP boundary, so no production code has a test mode). Run
  `aba17e42-e213-4020-973e-5fb6cad8df0b`: complete, 9 supported / 1 unsupported / 2 unreachable; the
  verifier's trace shows both injected URLs rejected with 0 hops. Traces in `traces/a2-fault-unsafe-urls/`.
- Getting there produced three extra runs, kept in run history with their real status (listed in
  ORCHESTRATION.md): another set of my services was already on ports 8000–8002, so the first run
  bypassed the proxy; then my researcher and verifier had stopped, giving one researcher-failed and
  one verifier-failed run. Nothing was deleted.

## November 7 — deploying the verifier on Render

- **Deployment.** Render web service `fnms-verifier`, free plan ($0/month; Render pre-selected a $7
  plan, which I switched to free), "Public Git Repository" source (no Git provider linked to Render),
  branch `main` at `b702751`, build `pip install -r verifier/requirements.txt`, start
  `uvicorn verifier.app:app --host 0.0.0.0 --port $PORT`, health check `/healthz`, env
  `PYTHON_VERSION=3.11.9`, `VERIFIER_TRACE_STDOUT=1`. `VERIFIER_TOKEN` and `GEMINI_API_KEY` are set
  only in Render's Environment page (same token value as my local `.env`).
- **D22.** The repository root `.env` sets `VERIFIER_URL=https://fnms-verifier.onrender.com`, so every
  orchestrated run uses the Render verifier. The URL is not a secret; the file stays git-ignored.
- **Live test table against Render:** 16/16
  ([traces/a2-render/matrix-verifier-warm.json](traces/a2-render/matrix-verifier-warm.json)).
- **D23.** `render.yaml` is kept as documentation of the same settings; the service itself was created
  in the dashboard (Blueprints need a connected Git provider, which I didn't want).
- **UI check.** I checked the Tracker page: all four labels, excerpts for supported claims, the
  unreachable fault claims, complete/partial and failed-worker status in run history.
- **D24 (reversed the same day by D25, below):** at first `backend/.env` (the Neon connection string)
  stayed committed, as in 1B, since it is a throwaway course database.

## November 7 — first code review fixes

The first review turned up three problems that my happy-path tests had missed. I reproduced each one with fakes before changing the implementation, so the regression tests do not need paid model calls.

- **A malformed worker error crashed the run before it was saved.** A worker answering HTTP 500/503
  with `{"error": "bad"}` made `worker_client.py` call `.get()` on a string; the run never reached the
  report or the database (PDF §5–6). Fix: `_error_field()` reads `error.code`/`error.message` only if
  `error` is an object; and in `service.py` any unexpected exception while calling or reading ONE
  worker marks that worker failed (full share charged) instead of escaping. 24 new cases in
  `tests/orchestrator_check.py` (HTTP 500/503/400/200 × `"bad"`/list/number × both workers).
- **Workers could exceed their budget share** (PDF §8). The verifier counted a step only after a
  successful model response (3 calls with `max_steps=1`); the researcher's final finish call ignored
  `max_steps` (a call with `max_steps=0`) and tokens were checked only after spending them (a call with
  `max_tokens=1`). Fix, D26: a step and a token reservation are taken **before every model request** —
  first try, retry, fallback model and the final finish call alike (`Budget.reserve_step` in
  `tracker/budget.py`). A failed request still counts as a step. New checks in `tests/verifier_check.py`
  and `tests/researcher_check.py`. This also changes the A1B CLI's counting (retries now count as
  steps), which is the more honest count; its tests still pass.
- **D25 (reverses D24): `backend/.env` is no longer committed.** It held the live Neon connection
  string and the JWT secret in a public repository, which doesn't meet §14. Now `backend/.env` is
  git-ignored and untracked (`git rm --cached`; the local file stays), `backend/.env.example` has
  placeholders, and the README says to copy it. I rotated the JWT secret (existing logins must sign in
  again), and the Neon password has since changed, so the connection string in the git history of both
  public repositories no longer works; the new one exists only in the git-ignored local file. Verified
  after the change: direct connection, `api_check` 68/68, `tracker_api_check` 65/65, and a complete
  orchestrated run saved to Neon. The history is not rewritten. Trade-off: a grader needs a
  `DATABASE_URL` to run the backend (shared privately, or their own Neon database: `create_all` builds
  the tables on first start).
- **Render redeployed with the fixes** (manual deploy of `f616273`, 2026-11-07 23:11 UTC; a service built
  from a public Git URL does not auto-deploy on push). Live test table against it: 16/16
  ([traces/a2-render/matrix-verifier-after-fix.json](traces/a2-render/matrix-verifier-after-fix.json)).
  Live budget check: two claims on two pages with `max_steps=1` → HTTP 200 `partial`,
  `budget_exhausted`, usage `{steps: 1, fetches: 2, tokens: 371}`, one verdict
  ([traces/a2-render/budget-max-steps-1.json](traces/a2-render/budget-max-steps-1.json)). Known nit, not
  fixed: the verifier fetches a page before checking whether a model step is left for it, so it can
  spend one fetch it can't use (still within `max_fetches`).

## November 7 — second code review fixes

The next pass focused on budget accounting, request logs, deadlines, and the amount of page text shown to the verifier. I added offline tests for the changes.

- **D27: token limits are an upper bound, not an estimate.** Before, both workers reserved
  `characters ÷ 3 + max output`, which is a heuristic, and a failed call gave back its reservation even
  when its usage was unknown. Now, before every model request:
  - *input bound* = UTF-8 bytes of everything sent plus a small per-message overhead
    (`input_token_bound` in `tracker/agent.py` and `verifier/judge.py`). A byte-fallback tokenizer
    (SentencePiece, which Gemini uses) never emits more tokens than bytes, so this holds for any language.
    **Assumption, not a provider guarantee**: Google doesn't document it. It over-reserves 3–4× for English.
  - *output cap* = `min(max_output_tokens, tokens left − input bound)`, sent to the model as
    `maxOutputTokens` for that call; no call starts if the cap would be under 256. So input bound + cap
    never exceeds what is left. Thinking tokens: this model reported 0 in every trace; for a thinking
    model, whether `maxOutputTokens` bounds them is not verified.
  - *after the call*: success → the reservation is replaced by the provider's real count. Refused before
    processing (429, any 4xx) → reservation released. Failed after possibly being processed (timeout,
    5xx, network, unexpected error) → **the whole reservation stays charged** as unknown usage
    (`tokens_unknown` in the researcher's stats), so `usage.tokens` can only over-count.
- **D28: every inbound request is logged** (§13). Backend: middleware writes one line per request
  (method + route template, status, latency, `run_id` when the route has one) through the same writer
  the orchestrator uses (`backend/app/orchestrator/tracing.py`). Headers, query strings and bodies are
  never logged. Workers: `POST /verify|/research` and `GET /healthz` already logged themselves; a
  middleware in `shared/workerkit.py` logs everything else (`GET /trace`, unknown paths), so nothing is
  logged twice.
- **D29: deadlines checked per attempt.** The researcher's search timeout is recomputed before each
  retry from the time actually left. The orchestrator's `max_seconds_per_worker` is an overall limit:
  each attempt's connect/read timeouts are cut to what is left of it. (The first version still let a
  trickling worker stretch an attempt; fixed in round 3, D31.)
- **D30: long pages, relevant passages.** For a page longer than the 15,000-character window the
  verifier sends the model the first 2,000 characters plus the lines sharing the most claim words (with
  neighbours), in page order, `[…]` between gaps (`select_passages` in `verifier/judge.py`). The evidence
  check still runs on the whole fetched page, and a quote stitched across a `[…]` gap fails it (tested).
  Runs 1–3, the hand-check and the Render run were measured before this change and are not re-labelled.
- **Render redeployed with `8da627a`** (manual deploy, 2026-11-07 23:39 UTC). Live checks against it:
  test table 16/16 ([traces/a2-render/matrix-verifier-after-review2.json](traces/a2-render/matrix-verifier-after-review2.json));
  `max_steps=1` → 1 step, `partial`, `budget_exhausted`; `GET /trace` requests now appear in Render's
  own log. Hand-check claim #3 sent to Render: `unreachable` (the site returns HTTP 403 to Render's
  address). Same claim through the new code on my laptop with the real model: `supported`, with the
  correct, code-checked excerpt. All in [traces/a2-render/after-review2-checks.json](traces/a2-render/after-review2-checks.json).

## November 7 — third code review fixes

I reproduced both problems first: with the previous code, a worker sending its answer one byte every
0.4 s held a call with `max_seconds_per_worker=2` for 8.4 s and the answer was accepted; and a CORS
preflight (`OPTIONS`) got HTTP 200 with no trace line. Both new tests fail on the old code and pass on
the new.

- **D31: strict total deadline per worker call.** Each attempt runs in a helper thread
  (`WorkerClient._attempt` in `backend/app/orchestrator/worker_client.py`): connect, headers and the
  whole body must arrive within the time left of `max_seconds_per_worker`, measured by the caller. Past
  it the attempt is a timeout (`DeadlineExceeded`, a subclass of httpx's `TimeoutException`, so the
  normal retry/give-up path applies) and its connection is closed in the background. Answers larger
  than 2 MB are discarded unread, so they become an invalid body = worker failure. Test: trickling
  worker, 2 s limit → gives up at 2.0 s, not answered.
- **D32: the request logger is registered after CORS.** In Starlette the last-registered middleware
  is the outermost, so the logger wraps CORS and records the preflights that CORS answers by itself
  (`backend/app/main.py`). Test: `OPTIONS /api/tracker/runs` preflight → one trace line, 200.

## November 14 — verifier wording and trace redaction

- **D33: verifier instructions allow a less detailed claim.** The hand-check found 3 of 3
  "unsupported" verdicts wrong; one was the 15,000-character window (D30), the other two were
  over-strict instructions ("by itself states the claim", "only partly states it"). New rule in
  `verifier/judge.py`: a claim may leave out details its source gives and may rely on context the page
  establishes elsewhere (e.g. the year in the article's date), but anything it asserts beyond the source
  (different number/date/name/version, extra detail, stronger statement) is unsupported. The code's
  evidence check is unchanged, so this can't let an invented quote through. Live before/after on 6 real
  claims (3 true, 3 deliberately false variants): old 5/6, new 6/6, no false claim accepted
  ([traces/a2-tests/verifier-instructions-before-after.json](traces/a2-tests/verifier-instructions-before-after.json)).
  Small sample; earlier runs are kept as measured.
- **D34: URLs in traces are redacted.** `shared/tracelog.py` now rewrites every URL in every string it
  logs, in all three services: query values become `REDACTED` (names kept), `user:password@` is
  removed, the `#fragment` dropped. Before, a cited URL carrying `?token=...` would have been written to
  the verifier's `url` field as-is. Tests in `tests/contract_check.py` (the `url` field and free text).
- **Render redeployed with `9a30d09`** (manual deploy, 2026-11-15 UTC). Live checks against it:
  test table 16/16 ([traces/a2-render/matrix-verifier-after-review4.json](traces/a2-render/matrix-verifier-after-review4.json));
  the same 6 claims as the before/after check, on Render: 6/6 correct (3 true → supported, 3 false
  variants → unsupported); a claim citing `https://example.com/?token=SECRET-QUERY-VALUE-123&page=2`:
  Render's own log holds `https://example.com/?token=REDACTED&page=REDACTED` and not the value
  ([traces/a2-render/after-review4-checks.json](traces/a2-render/after-review4-checks.json)).
- **Credentials, re-checked.** The JWT secret was rotated and the Neon password changed on 2026-11-07
  (D25), so the old values in the git history no longer work; the current ones exist only in
  git-ignored local files and in Render's settings.

## November 15 — end-to-end check and broader verifier tests

- **End-to-end check before the last verifier fixes.** The backend and researcher ran on my laptop; the verifier was deployed on Render at `9a30d09`. This run finished **before** the later `2d6c0a3` release added typography folding and evidence repair, so it is not an end-to-end test of those fixes. Run `7a161759-11d1-40fe-95b6-5bd5c0d39132`, complete, 4 developments, 4 claims all
  supported with excerpts, saved to Neon, returned by `/runs/latest` and first in run history. The
  verifier's 47 s include one Gemini 429 and its `Retry-After` wait. All three logs in
  [traces/a2-final-run/](traces/a2-final-run/); the orchestrator log now also shows the request-logging
  lines (login, progress polls).
- **D35: typography folding** as a fifth normalization step, both strings (`verifier/evidence.py`):
  curly quotes and dashes → ASCII. Stated in ORCHESTRATION.md Q4.
- **D36: one repair call for rejected excerpts** (`verifier/core.py`): when the code rejects a
  "supported" excerpt (too long / not on the page), the model is asked once more, for those claims only,
  with the reason; the answer goes through the same check and costs a step. Never an upgrade without a
  checked excerpt. The evidence-check log line now records which claim ids were downgraded and why.
- **D37: larger verifier test** (`tests/verifier_eval.py`). I reused 56 saved claims and made 47 number-changed variants, then ran the same set four times. Of the 46 variants actually judged in each run, none was marked `supported`: **0/46 per run, or 0/184 repeated judgments**. That does not mean 184 independent false claims or a measured population false-positive rate. A digit change could accidentally produce another supported fact, and I did not hand-check every variant. On the deployed version, the 56 original claims returned 53 supported, 2 unsupported and 1 unreachable; these are verdict counts, not independently measured accuracy. The details are in `ORCHESTRATION.md` Q5.
- **Render redeployed with `2d6c0a3`** (manual deploy, 2026-11-15 UTC): test table 16/16
  ([traces/a2-render/matrix-verifier-final.json](traces/a2-render/matrix-verifier-final.json)); larger
  test on Render as above ([traces/a2-tests/verifier-eval-20261115-020529.json](traces/a2-tests/verifier-eval-20261115-020529.json)).
