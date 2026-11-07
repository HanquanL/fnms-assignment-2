# Assignment 2 — decision and conflict log

Written while implementing Assignment 2 (agentic orchestration). The instructor's PDF
(`FNMS Fall 2026 Assignments 2.pdf`) is the source of truth; `command.md` / `claude.md` (identical files,
written with ChatGPT) operationalize it. Each entry says what was decided and why, so every part can be
defended at the demo.

## Baseline (before any change), 2026-11-04

| Check | Result |
|---|---|
| `tests/guard_check.py` | 34 passed, **1 failed**: "redirect to loopback is rejected at the redirect hop" — the test depends on a live public redirect service, which answered `HTTP 403`. Not a code regression; network-dependent. |
| `tests/agent_check.py` | 47 passed, 0 failed |
| `tests/failure_check.py` | 29 passed, 0 failed |
| `tests/memory_check.py` | 11 passed, 0 failed |
| Neon database | reachable; 3 runs in `tracker_runs`; 6 tables |
| A1B single-agent baseline (from committed traces) | run1: 16 model calls, 7 searches, 6 fetches, 165,064 tokens, 68.4 s. run2: 16 / 7 / 2 fetches, 181,553 tokens, 68.6 s |

Uncommitted work found on branch `run-button` (`tracker/__main__.py`, `tracker/backend.py`: a `TRACKER_TOKEN`
login path) was left untouched and is not part of the A2 commits. A2 work happens on branch
`a2-orchestration`.

## Conflicts between command.md and the PDF (PDF wins)

| # | command.md says | PDF says | Resolution |
|---|---|---|---|
| C1 | §0.3: ask before costly live runs, deployment, external pushes | (nothing) | The student explicitly said "full access, don't ask me to approve anything". Live free-tier runs were made without asking. Actions the safety rules forbid me to do (typing API keys into the Render dashboard, creating accounts) are left to the student and listed in README "Render deployment". |
| C2 | §4.1 "show the **last successful** run's top K as stale" | §6 "shows **last run's** top K, marked stale" | If the previous run was itself a researcher failure it has no top K of its own, so "last run's top K" would be empty. Implemented: the most recent run that has a ranked top K. In the common case (previous run succeeded) both readings agree. |
| C3 | §5.5 a supported excerpt must "substantiate the claim" (checked by code) | §9 code checks only that the excerpt (≤300 chars) appears in the fetched text | Substantiation is a semantic judgment; code cannot decide it. Code enforces what the PDF requires (length, presence after normalization) plus a minimum excerpt length (documented in ORCHESTRATION.md as an extra rule, not a normalization). The model is asked to pick an excerpt that substantiates the claim. |
| C4 | §9 test matrix: "researcher rejects unsafe fetch too" for `http://169.254.169.254/` | p.7 test table lists the metadata-URL test as a claim test | The researcher's payload has no URLs (`{topic, k}`), so the test cannot be sent to it as a request. The researcher reuses `tracker/urlguard.py`, whose existing guard tests (`tests/guard_check.py`) cover it; the verifier test sends the URL in a claim. |
| C5 | §3 "Successful result body: `{"developments": [...]}`" plus suggested `status`/`usage`/`error` | p.3–4: table column is "result out"; p.4 "On partial or failed, `error` says why"; 500 → `status: failed, retryable: false` | The response envelope is `{request_id, run_id, status, result, usage, error}`; `result` holds exactly the PDF's "result out" object. The field names `status`, `error.code/message/retryable` come from the PDF; `usage` and the `result` wrapper are my choice (the PDF asks workers to report usage but doesn't name the field). |
| C6 | §4.3 keep `max_searches` | p.3 envelope budget has exactly `max_steps`, `max_fetches`, `max_tokens` | The wire budget keeps exactly the PDF's three keys. Search calls are capped by the researcher's own policy (`researcher.max_searches` in `config.yaml`), and reported in `usage.searches` for cost accounting. |
| C7 | §1 repository is `fnms-assignment-1b` | — | The student decided (2026-11-04) the A2 project is `fnms-assignment-2` and **nothing goes to `fnms-assignment-1b`**. Before that instruction I had pushed branch `a2-orchestration` there (commits `7a0a722`, `496ca88`); see "Live runs" below. The local `origin` still points at the 1B repository (re-pointing it was blocked by a permission check), so nothing more is pushed from this checkout until the student sets the remote. |
| C8 | §2/§8 Phase 5: deploy the verifier to Render's free tier | p.2 "Deploy your verifier on Render's free tier" | 2026-11-04 the student first chose to run everything locally; on **2026-11-07 the verifier was deployed to Render's free tier** as the PDF asks: `https://fnms-verifier.onrender.com`, built from the public repository `HanquanL/fnms-assignment-2`, branch `main`. The orchestrator and researcher still run locally (allowed by the PDF). Runs 1–3 and the 2026-11-04 fault runs used a local verifier and are kept as measured; the Render run is reported separately in ORCHESTRATION.md. |

## Design decisions

| # | Decision | Why |
|---|---|---|
| D1 | Orchestrator is **plain code** inside the existing FastAPI backend (`backend/app/orchestrator/`). No LLM routing. | The flow is fixed (research → verify → report). Nothing in it needs judgment; code is cheaper, testable, and can't be prompt-injected. |
| D2 | `shared/` holds the wire contract (Pydantic models, error codes, canonical JSON hash) and two stateless helpers (JSONL trace writer, worker HTTP plumbing: auth, 400 mapping, idempotency registry class). No service imports another service's package. | PDF §2 forbids importing *another service's code to call it*; it allows a shared schema file. The helpers hold no state shared between processes: each process creates its own registry and trace file. Deviates from the earlier note "shared holds schemas only" to avoid three copies of the same 400/401/409/503 logic, which would be harder to keep consistent. |
| D3 | The verifier gets its **own copy** of the fetch guardrails (`verifier/fetchguard.py`, from `tracker/urlguard.py` + `tracker/fetch.py` + `tracker/extract.py`), not an import of `tracker/`. | `tracker/` is the researcher's code. The verifier deploys alone to Render and must not depend on it (agreed earlier with the student). |
| D4 | Every redirect hop is one fetch. `fetch_article` gained an `on_hop` callback that is called **before** each outgoing request and may refuse it (budget). The researcher's worker budget counts hops, not `fetch_article` calls. | PDF §3: "A fetch is one HTTP request to a web page, redirects included." |
| D5 | One Gemini key for both workers, called **sequentially** (researcher, then verifier); each worker paces its own model calls. | Free tier; running both at once would contend for the same per-minute quota. |
| D6 | Verifier: one fetch per **distinct URL** per request (cached within that request only), and one model call per distinct URL that judges all claims citing it. | Cuts fetches and model calls when several claims cite the same article. The cache lives only for one request, so nothing leaks between runs. |
| D7 | Verifier model output is JSON (Gemini `responseMimeType: application/json`); code validates every field, ignores claim ids it didn't ask about, downgrades `supported` when the excerpt check fails, and never upgrades. | PDF §9 and §11. |
| D8 | The researcher worker runs with **no memory** (no DB, no seen-URL list). The orchestrator does key dedupe against known developments and new/still/dropped classification (logic re-implemented in `backend/app/orchestrator/memory.py`). | PDF: only the orchestrator reads or writes state. The A1B tracker's memory came from the database. |
| D9 | No `ALTER TABLE` on existing tables. New table `tracker_claims` (created by `create_all`) stores each claim, its URL, its verdict and the verifier's evidence. Which worker failed and the stale-fallback run id are stored in the run's existing `stats` JSON and exposed as explicit API fields. | `create_all` creates new tables but never alters existing ones; avoiding a migration on the shared Neon database removes the riskiest step. |
| D10 | Researcher evidence quotes are **not** on the wire (contract has `claim_id, text, source_url` only). `tracker_sources.evidence` is left `NULL` for A2 runs; the only evidence shown is the verifier's, from `tracker_claims`. | Keeps the researcher's and the verifier's evidence from being confused (command.md §6). |
| D11 | Runs are started from the web page (`POST /api/tracker/orchestrate`, user JWT) or the CLI (`python -m orchestrator_cli`), run in a background thread, and use the orchestrator `run_id` as the `tracker_runs.id`. | One id ties the DB row to all three trace logs. |
| D12 | At the start of each run the orchestrator sends `GET /healthz` to the verifier in the background, while the researcher works. | Wakes a sleeping Render instance during the ~1–2 minutes the researcher needs, so the cold start mostly overlaps useful work. The real `/verify` timeout still allows for a full cold start in case the wake-up fails. |
| D13 | Idempotency registry is in memory (per process), keyed by `request_id`, value = SHA-256 of the canonical JSON body (sorted keys, no whitespace) + state (in-flight / done) + stored status code and body. Only final answers (200, 500) are stored; 503s are not. Bounded: oldest finished entries evicted past 1,000 entries. | PDF §7. A Render restart loses it (discussed in ORCHESTRATION.md Q3). |
| D14 | Worker response envelope: `{request_id, run_id, status, result, usage, error}` (see C5). `usage = {steps, fetches, tokens, searches}`; `searches` is 0 for the verifier. | Same shape for both workers; orchestrator validates it identically. |
| D15 | Orchestrator charging: valid response → the usage it reports (must be ≤ the share it was given, else invalid); no answer / timeout / malformed / 5xx → the **full share**; 400/401 → 0 (the worker rejects these before doing any work). Each `request_id` charged once regardless of attempts. | PDF §8. |
| D16 | Traces: each service appends to its own file, `traces/<service>.jsonl` (verifier on Render: also stdout, plus an authenticated `GET /trace?run_id=` to download its log). Required fields always present; extra fields allowed. Inbound requests are logged once when answered, with server-side handling time; caller-side latency is logged by the caller on its own monotonic clock. | PDF §13. |
| D17 | `config.yaml` keeps the A1B `limits:` block next to the new `run_budget`/`allocation`. | `limits` is only read by the single-agent CLI (`python -m tracker run`), kept runnable as the baseline for ORCHESTRATION.md Q5; A2 runs use only `run_budget`/`allocation`. Commented in config.yaml. |
| D18 | Worker URLs default to `http://127.0.0.1:800x`, not `localhost`. | Found in run 1's traces: on Windows `localhost` tries `::1` first, uvicorn listens on IPv4 only, and every call paid ~2 s (4 s for a refused connection). Changed after the measured runs; the runs' numbers are reported as measured. |
| D19 | Evidence quotes in the researcher's report are dropped at the wire boundary (`researcher/core.py` `to_wire`). | Contract: the verifier must not see them, and the orchestrator has no use for them (D10). |
| D20 | The hand-check in ORCHESTRATION.md Q5 was done twice: by Claude (2026-11-04, fetching each page again and reading it) and independently by the student (2026-11-07, in a browser). Both agree on all 13 verdicts; the student's notes are in the table's last column. | The student must be able to defend it; the table quotes the page text so it can be re-checked quickly. |

## Live runs and what was NOT done (2026-11-04)

- Done live, local verifier: worker test matrix for both workers (16/16 verifier, 13/13 researcher), runs 1–3 complete, one verifier-down and one researcher-down fault-injection run. Every number in ORCHESTRATION.md comes from these traces.
- Branch `a2-orchestration` was pushed to `origin` = `fnms-assignment-1b` (commits `7a0a722`, `496ca88`) to let Render build it. The student then said nothing should go to `fnms-assignment-1b`; no further pushes were made. Whether to delete that remote branch is the student's call. `main` was never touched; nothing was force-pushed.
- **Not done on 2026-11-04: Render deployment** (done on 2026-11-07, see below and C8).
- **Not visually checked on 2026-11-04: the React page** (the student checked it on 2026-11-07, see below). It lints and builds (`npm run build`), and the API it reads is covered by `tests/orchestrator_check.py`, but the built-in browser can't open the local dev server, so no one has looked at it yet.

## 2026-11-07: unreachable label shown end to end

- No real run had produced an `unreachable` claim, so the UI had never shown that label. Added
  `tests/fault_proxy.py`, a test-only proxy that forwards `/research` to the real researcher and
  appends two claims labelled "FAULT INJECTION TEST" citing `169.254.169.254` and `localhost`
  (D21: done at the HTTP boundary, so no production code has a test mode). Run
  `aba17e42-e213-4020-973e-5fb6cad8df0b`: complete, 9 supported / 1 unsupported / 2 unreachable; the
  verifier's trace shows both injected URLs rejected with 0 hops. Traces in `traces/a2-fault-unsafe-urls/`.
- Getting there produced three extra runs, kept in run history with their real status (listed in
  ORCHESTRATION.md): the student's own services were already on ports 8000–8002, so the first run
  bypassed the proxy; then the student's researcher and verifier had stopped, giving one
  researcher-failed and one verifier-failed run. Nothing was deleted.

## 2026-11-07: verifier on Render

- **Deployment.** Render web service `fnms-verifier`, free plan ($0/month; Render pre-selected a $7
  plan, switched to free), "Public Git Repository" source (no GitHub account linked to Render),
  branch `main` at `b702751`, build `pip install -r verifier/requirements.txt`, start
  `uvicorn verifier.app:app --host 0.0.0.0 --port $PORT`, health check `/healthz`, env
  `PYTHON_VERSION=3.11.9`, `VERIFIER_TRACE_STDOUT=1`. The student signed in and pasted
  `VERIFIER_TOKEN` and `GEMINI_API_KEY` into Render's Environment page themselves; I checked only that
  both were filled and that their lengths matched the local `.env`, without reading them.
- **D22.** The repository root `.env` now sets `VERIFIER_URL=https://fnms-verifier.onrender.com`, so
  every orchestrated run uses the Render verifier. The URL is not a secret; the file stays git-ignored.
- **Live test table against Render:** 16/16
  ([traces/a2-render/matrix-verifier-warm.json](traces/a2-render/matrix-verifier-warm.json)).
- **D23.** `render.yaml` is kept as documentation of the same settings; the service itself was created
  in the dashboard (Blueprints need a connected Git provider, which the student didn't want).
- **UI check.** The student looked at the Tracker page on 2026-11-07 (all four labels, excerpts, the
  unreachable fault claims, run history) and reported that everything looked right.
- **D24 (reversed the same day by D25, below): database credentials stay public.** `backend/.env` (the Neon connection string) is committed
  in the public `fnms-assignment-2` repository, as it was in 1B. The student accepted this: it is a
  throwaway course database. Anyone with the repository can read and write it.

## 2026-11-07: fixes from an external review

An external review of the pushed repository against the PDF raised three P1 findings. All three were
reproduced with fakes (no paid calls) before fixing, and each fix has regression tests.

- **Finding 3: a malformed worker error crashed the run before it was saved.** A worker answering
  HTTP 500/503 with `{"error": "bad"}` made `worker_client.py` call `.get()` on a string; the run never
  reached the report or the database (PDF §5–6). Fix: `_error_field()` reads `error.code`/`error.message`
  only if `error` is an object; and in `service.py` any unexpected exception while calling or reading
  ONE worker marks that worker failed (full share charged) instead of escaping. 24 new cases in
  `tests/orchestrator_check.py` (HTTP 500/503/400/200 × `"bad"`/list/number × both workers).
- **Finding 2: workers could exceed their budget share** (PDF §8). The verifier counted a step only
  after a successful model response (3 calls with `max_steps=1`); the researcher's final finish call
  ignored `max_steps` (a call with `max_steps=0`) and tokens were checked only after spending them (a
  call with `max_tokens=1`). Fix, D26: a step and a token reservation are taken **before every
  model request** — first try, retry, fallback model and the final finish call alike
  (`Budget.reserve_step` in `tracker/budget.py`, `estimate_tokens` in `tracker/agent.py`; the verifier
  already reserved tokens and now also reserves the step first). A failed request still counts as a step.
  New checks in `tests/verifier_check.py` and `tests/researcher_check.py`. This also changes the A1B CLI's
  counting (retries now count as steps), which is the more honest count; its tests still pass.
- **Finding 1, D25 (reverses D24): `backend/.env` is no longer committed.** It held the live Neon
  connection string and the JWT secret in the public repository, which the review correctly says
  doesn't meet §14. Now: `backend/.env` is git-ignored and untracked (`git rm --cached`; the local file
  stays), `backend/.env.example` has placeholders, and the README says to copy it. The JWT secret was
  rotated locally (existing logins must sign in again). **The student decided not to reset the Neon
  password (2026-11-07).** Known residual risk, accepted: the current Neon connection string is still in
  the git history of both public repositories (`fnms-assignment-1b`, `fnms-assignment-2`) and still
  works, so anyone who reads that history can read and write the tracker database. Untracking the file
  stops publishing it in new commits; only a password reset would make the old copies useless. The
  history is not rewritten. Trade-off: a grader now needs a `DATABASE_URL` to run the backend (shared
  privately, or their own Neon database: `create_all` builds the tables on first start).
- **Render redeployed with the fixes** (manual deploy of `f616273`, 2026-11-07 23:11 UTC; a service built
  from a public Git URL does not auto-deploy on push). Live test table against it: 16/16
  ([traces/a2-render/matrix-verifier-after-fix.json](traces/a2-render/matrix-verifier-after-fix.json)).
  Live budget check: two claims on two pages with `max_steps=1` → HTTP 200 `partial`,
  `budget_exhausted`, usage `{steps: 1, fetches: 2, tokens: 371}`, one verdict
  ([traces/a2-render/budget-max-steps-1.json](traces/a2-render/budget-max-steps-1.json)). Known nit, not
  fixed: the verifier fetches a page before checking whether a model step is left for it, so it can
  spend one fetch it can't use (still within `max_fetches`).

## 2026-11-07: second review (P2 recommendations)

All five recommendations were taken. Each has offline tests; none needed a paid call.

- **D27: token limits are an upper bound, not an estimate.** Before, both workers reserved
  `characters ÷ 3 + max output`, which is a heuristic (the docs wrongly called it "worst-case"), and a
  failed call gave back its reservation even when its usage was unknown. Now, before every model request:
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
  never logged. Workers: `POST /verify|/research` and `GET /healthz` already logged themselves; a new
  middleware in `shared/workerkit.py` logs everything else (`GET /trace`, unknown paths), so nothing is
  logged twice.
- **D29: deadlines checked per attempt.** The researcher's search timeout is recomputed before each
  retry from the time actually left. The orchestrator's `max_seconds_per_worker` is now an overall limit:
  each attempt's connect/read timeouts are cut to what is left of it. Residual: httpx's read timeout
  is per read, so a worker that trickles bytes could still stretch one attempt; our workers answer
  with one small JSON body.
- **D30: long pages, relevant passages.** For a page longer than the 15,000-character window the
  verifier sends the model the first 2,000 characters plus the lines sharing the most claim words (with
  neighbours), in page order, `[…]` between gaps (`select_passages` in `verifier/judge.py`). The evidence
  check still runs on the whole fetched page, and a quote stitched across a `[…]` gap fails it (tested).
  Runs 1–3, the hand-check and the Render run were measured before this change and are not re-labelled.
- **Not done here:** Render runs the verifier from GitHub, so these changes reach it only after a push
  and a manual redeploy.
