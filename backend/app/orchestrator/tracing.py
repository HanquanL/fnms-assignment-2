"""The orchestrator process's one trace log (PDF §13), shared by the request-logging middleware
(every request the backend receives) and the orchestrator (every call it makes), so all of this
process's lines go through one writer and one lock."""
import os
import time
from functools import lru_cache
from pathlib import Path

from shared.tracelog import TraceLog

ROOT = Path(__file__).resolve().parents[3]


def trace_path() -> Path:
    return Path(os.environ.get("ORCHESTRATOR_TRACE_PATH") or ROOT / "traces" / "orchestrator.jsonl")


@lru_cache(maxsize=1)
def get_trace() -> TraceLog:
    return TraceLog("orchestrator", trace_path())



def install_request_logging(app) -> None:
    """One JSONL line per HTTP request the backend receives: method + route template, HTTP status,
    latency on this process's monotonic clock. run_id when the route has one (or the handler sets
    request.state.run_id). Headers, query strings and bodies are never logged, so no credential
    (Authorization header, password in a login body) can reach the file."""

    @app.middleware("http")
    async def log_request(request, call_next):
        t0 = time.monotonic()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", None) or request.url.path
            run_id = request.scope.get("path_params", {}).get("run_id") or getattr(request.state, "run_id", None)
            get_trace().log(run_id=str(run_id) if run_id else None, request_id=None, step=None, direction="in",
                            endpoint_or_tool=f"{request.method} {template}", status=status,
                            latency_ms=int((time.monotonic() - t0) * 1000), attempt=None, tokens=0,
                            http_status=int(status))
