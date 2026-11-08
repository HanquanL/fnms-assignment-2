"""Calling a worker over HTTP: timeouts, bounded retries, and the caller-side trace (PDF §6, §7, §13).

Retry policy (only for failures where trying again can help):
    retried    connection refused/reset, DNS failure, connect or read timeout, HTTP 503
               (and 502/504, which a proxy such as Render's sends while an instance is starting)
    never      200 (complete or partial), 400, 401, 409, 500, any other status
Backoff is exponential with jitter, capped, and never shorter than Retry-After. A Retry-After
longer than max_retry_after_seconds ends the call (waiting that long isn't a "blip").

A retry resends EXACTLY the same bytes: the envelope is serialized once, before the first attempt,
and the same request_id goes out every time (a changed body would be a different request -> 409).

Latency is measured here, on the orchestrator's monotonic clock, for every attempt.
"""
import concurrent.futures
import json
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from app.orchestrator.settings import OrchestratorSettings, WorkerSettings
from shared.tracelog import TraceLog

TRANSIENT_STATUSES = (502, 503, 504)
MAX_RESPONSE_BYTES = 2_000_000      # a worker answer is a small JSON object; anything bigger is invalid


class DeadlineExceeded(httpx.TimeoutException):
    """No complete answer (connect + headers + whole body) within the time left of
    max_seconds_per_worker. httpx's own read timeout only bounds each wait between chunks, so a
    worker trickling bytes could otherwise stretch an attempt indefinitely."""


@dataclass
class CallResult:
    worker: str
    request_id: str
    answered: bool              # a final HTTP answer arrived (any status that isn't retried)
    http_status: int | None
    body: dict | None           # parsed JSON, NOT yet validated
    attempts: int
    error: str | None           # why the call failed, for the report / run history
    latency_ms: int             # whole call incl. retries and backoff, caller-side


def _error_field(data: dict | None, key: str) -> str | None:
    """error.<key> from an UNVALIDATED body. The body comes from a worker and may be malformed
    ("error": "bad", "error": [1], no error at all), so never assume its shape: anything that isn't
    an object with a scalar value yields None. Validation happens later, in validate.py."""
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return None
    value = err.get(key)
    return str(value)[:200] if isinstance(value, (str, int, float, bool)) else None


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        v = resp.headers.get("retry-after")
        return max(0.0, float(v)) if v is not None else None
    except ValueError:
        return None


class WorkerClient:
    def __init__(self, settings: OrchestratorSettings, trace: TraceLog, *,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.s, self.trace, self.transport, self.sleep, self.clock = settings, trace, transport, sleep, clock
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="worker-call")

    def _client(self, w: WorkerSettings, read_timeout: float) -> httpx.Client:
        c = self.s.connect_timeout_s
        return httpx.Client(timeout=httpx.Timeout(connect=c, read=read_timeout, write=c, pool=c),
                            transport=self.transport, follow_redirects=False)

    def _attempt(self, w: WorkerSettings, url: str, body: bytes, headers: dict, timeout: httpx.Timeout,
                 left: float) -> httpx.Response:
        """ONE request, with a strict total deadline: connect, headers AND the whole body must be done
        within `left` seconds, measured here. Past it, the attempt is a timeout (DeadlineExceeded) and
        its connection is closed in the background so a slow worker can't hold this run."""
        client = self._client(w, timeout.read)

        def run() -> httpx.Response:
            with client.stream("POST", url, content=body, headers=headers, timeout=timeout) as r:
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_RESPONSE_BYTES:   # not a valid answer: keep nothing of it
                        buf = bytearray()
                        break
                keep = {k: v for k, v in r.headers.items() if k.lower() in ("retry-after", "content-type")}
                return httpx.Response(r.status_code, headers=keep, content=bytes(buf))

        future = self._pool.submit(run)
        try:
            return future.result(timeout=max(0.0, left))
        except concurrent.futures.TimeoutError:
            threading.Thread(target=client.close, daemon=True).start()   # unblocks and ends run()
            raise DeadlineExceeded(f"no complete answer within the {left:.1f}s left of max_seconds_per_worker")
        finally:
            if future.done():
                client.close()

    def call(self, w: WorkerSettings, envelope: dict, *, step: int) -> CallResult:
        rid, run_id = envelope["request_id"], envelope["run_id"]
        body_bytes = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")  # once
        headers = {"Authorization": f"Bearer {w.token}", "Content-Type": "application/json"}
        url = f"{w.url}{w.path}"
        endpoint = f"POST {w.path}"
        read_timeout = self.s.response_timeout(w)
        policy = self.s.retry
        started = self.clock()
        last_error = "not attempted"

        for attempt in range(1, policy.max_attempts + 1):
            t0 = self.clock()
            wait: float | None = None
            # max_seconds_per_worker is an overall deadline for this worker: each attempt gets at
            # most the time that is left of it, not a fresh full response timeout
            left = self.s.max_seconds_per_worker - (t0 - started)
            if left < 1:
                self.trace.log(run_id=run_id, request_id=rid, step=step, direction="out", endpoint_or_tool=endpoint,
                               status="failed", latency_ms=0, attempt=attempt, tokens=0, worker=w.name,
                               detail="no time left of max_seconds_per_worker")
                return CallResult(w.name, rid, False, None, None, attempt - 1,
                                  f"{w.name} did not answer ({last_error}); out of time",
                                  int((self.clock() - started) * 1000))
            c = min(self.s.connect_timeout_s, left)
            attempt_timeout = httpx.Timeout(connect=c, read=min(read_timeout, left), write=c, pool=c)
            try:
                resp = self._attempt(w, url, body_bytes, headers, attempt_timeout, left)
            except httpx.TimeoutException as e:
                last_error = f"{type(e).__name__} after {self.clock() - t0:.1f}s"
                status = "timeout"
            except httpx.TransportError as e:
                last_error = f"{type(e).__name__}: connection failed"
                status = "connect_error"
            else:
                latency = int((self.clock() - t0) * 1000)
                try:
                    data = resp.json()
                    data = data if isinstance(data, dict) else None
                except ValueError:
                    data = None
                usage = (data or {}).get("usage") if isinstance((data or {}).get("usage"), dict) else {}
                tokens = usage.get("tokens") if type(usage.get("tokens")) is int else 0
                if resp.status_code not in TRANSIENT_STATUSES:
                    st = (data or {}).get("status") if resp.status_code == 200 else f"http_{resp.status_code}"
                    self.trace.log(run_id=run_id, request_id=rid, step=step, direction="out",
                                   endpoint_or_tool=endpoint, status=str(st or "invalid_body"),
                                   latency_ms=latency, attempt=attempt, tokens=tokens,
                                   http_status=resp.status_code, worker=w.name)
                    err = None if resp.status_code == 200 else (
                        f"HTTP {resp.status_code}: " + (_error_field(data, "message") or "no valid error message"))
                    return CallResult(w.name, rid, True, resp.status_code, data, attempt, err,
                                      int((self.clock() - started) * 1000))
                status = f"http_{resp.status_code}"
                code = _error_field(data, "code")
                last_error = f"HTTP {resp.status_code}" + (f": {code}" if code else "")
                wait = _retry_after(resp)
                if wait is not None and wait > policy.max_retry_after:
                    self.trace.log(run_id=run_id, request_id=rid, step=step, direction="out",
                                   endpoint_or_tool=endpoint, status="failed", latency_ms=latency,
                                   attempt=attempt, tokens=0, http_status=resp.status_code, worker=w.name,
                                   detail=f"Retry-After {wait:.0f}s exceeds {policy.max_retry_after:.0f}s")
                    return CallResult(w.name, rid, False, resp.status_code, None, attempt,
                                      f"{last_error}; asked to wait {wait:.0f}s, giving up",
                                      int((self.clock() - started) * 1000))

            latency = int((self.clock() - t0) * 1000)
            delay = min(policy.max_delay, policy.base_delay * 2 ** (attempt - 1)) + random.uniform(0, policy.base_delay)
            if wait is not None:
                delay = max(delay, wait)       # honour Retry-After
            out_of_attempts = attempt == policy.max_attempts
            out_of_time = (self.clock() - started) + delay > self.s.max_seconds_per_worker
            final = out_of_attempts or out_of_time
            self.trace.log(run_id=run_id, request_id=rid, step=step, direction="out", endpoint_or_tool=endpoint,
                           status="failed" if final else "retry", latency_ms=latency, attempt=attempt, tokens=0,
                           worker=w.name, detail=last_error, error_kind=status,
                           next_delay_s=None if final else round(delay, 1))
            if final:
                why = "attempts" if out_of_attempts else "time"
                return CallResult(w.name, rid, False, None, None, attempt,
                                  f"{w.name} did not answer ({last_error}); gave up after {attempt} attempt(s), "
                                  f"out of {why}", int((self.clock() - started) * 1000))
            self.sleep(delay)
        raise AssertionError("unreachable")

    def ping(self, w: WorkerSettings, *, run_id: str) -> tuple[bool, int]:
        """GET /healthz (no auth). Used to wake a sleeping Render instance early; never retried."""
        t0 = self.clock()
        try:
            with self._client(w, w.cold_start_s + self.s.slack_s) as client:
                r = client.get(f"{w.url}/healthz")
            ok = r.status_code == 200 and r.json() == {"status": "ok"}
            status = "ok" if ok else f"http_{r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            ok, status = False, type(e).__name__
        latency = int((self.clock() - t0) * 1000)
        self.trace.log(run_id=run_id, request_id=None, step=0, direction="out", endpoint_or_tool="GET /healthz",
                       status=status, latency_ms=latency, attempt=1, tokens=0, worker=w.name, purpose="prewarm")
        return ok, latency
