"""Shared HTTP for every outside service FFF talks to. All requests go through here.

One ``requests`` session per service, each with:

* a rate limiter shared by every thread (minimum spacing between requests, plus e621's
  pause every N calls);
* automatic retries for connection errors, timeouts, 429 and 5xx, honouring Retry-After,
  with a capped number of attempts. After the last attempt the final response is returned
  (or the exception raised), so callers' own status checks still apply;
* a default timeout and, for e621, the configured User-Agent.

A POST is only retried when the server cannot have acted on it (connection refused, or 429).
Repeating a POST after a 5xx could apply it twice, and for e621 votes a second vote undoes the
first, so it is never retried then. GET, PUT and DELETE are idempotent and retried normally.

Settings come from ``http.<service>`` in config.yaml, with ``DEFAULTS`` as fallback. The
defaults reproduce the spacing FFF used before (1 s for e621, none for the others).

``tests/test_http.py`` fails if a module outside ``fff/clients/http.py`` makes requests itself.
"""
from __future__ import annotations

import logging
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fff.common import config as fff_config

logger = logging.getLogger(__name__)

RETRY_STATUSES = (429, 500, 502, 503, 504)
DEFAULTS = {
    # e621 API and file downloads: 1 request/second and a 60 s pause every 5000 calls, as before.
    "e621": {"min_request_interval": 1.0, "max_retries": 5, "backoff": 2.0, "timeout": 30,
             "pause_every": 5000, "pause_seconds": 60, "retry_post": False},
    # e621 image previews in the review GUIs (static CDN, not the API): no spacing.
    "e621_files": {"min_request_interval": 0.0, "max_retries": 3, "backoff": 1.0, "timeout": 30,
                   "retry_post": False},
    "furaffinity": {"min_request_interval": 0.0, "max_retries": 4, "backoff": 2.0, "timeout": 30,
                    "retry_post": False},
    # Search endpoints: a POST that is only a lookup, so repeating it is harmless.
    "fuzzysearch": {"min_request_interval": 0.0, "max_retries": 3, "backoff": 2.0, "timeout": 30,
                    "retry_post": True},
    "fluffle": {"min_request_interval": 0.0, "max_retries": 3, "backoff": 2.0, "timeout": 30,
                "retry_post": True},
}

_lock = threading.RLock()  # re-entrant: session() builds a Session, which calls limiter()
_limiters: dict[str, "RateLimiter"] = {}
_sessions: dict[str, "Session"] = {}


def settings(service: str) -> dict:
    """DEFAULTS[service] overlaid with config.yaml's ``http.<service>`` (if there is a config)."""
    if service not in DEFAULTS:
        raise ValueError(f"unknown HTTP service {service!r}; expected one of {sorted(DEFAULTS)}")
    try:
        configured = (fff_config.load().get("http") or {}).get(service) or {}
    except fff_config.ConfigError:
        configured = {}
    return DEFAULTS[service] | configured


def _user_agent(service: str) -> str | None:
    if not service.startswith("e621"):
        return None  # FA sends the browser's UA (see furaffinity._fa_headers); the others need none
    try:
        return (fff_config.load().get("e621") or {}).get("user_agent")
    except fff_config.ConfigError:
        return None


class RateLimiter:
    """Minimum spacing between requests, shared by all threads, plus an optional long pause."""

    def __init__(self, interval: float, pause_every: int = 0, pause_seconds: float = 0, name: str = ""):
        self.interval = float(interval or 0)
        self.pause_every = int(pause_every or 0)
        self.pause_seconds = float(pause_seconds or 0)
        self.name = name
        self.count = 0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:  # sleeping under the lock queues other threads behind this request
            # perf_counter, not monotonic: on Windows before Python 3.13, monotonic() ticks only
            # every ~15.6 ms, so a spacing computed from it could come out short.
            delay = self._next - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            self.count += 1
            if self.pause_every and self.count % self.pause_every == 0:
                logger.info("Reached %s %s requests. Pausing %.0f seconds...",
                            self.count, self.name, self.pause_seconds)
                time.sleep(self.pause_seconds)
            self._next = time.perf_counter() + self.interval


def limiter(service: str) -> RateLimiter:
    with _lock:
        if service not in _limiters:
            s = settings(service)
            _limiters[service] = RateLimiter(s["min_request_interval"], s.get("pause_every", 0),
                                             s.get("pause_seconds", 0), name=service)
        return _limiters[service]


class _Retry(Retry):
    """urllib3 Retry that also repeats a POST answered with 429 (the server did not act on it)."""

    def is_retry(self, method, status_code, has_retry_after=False):
        if status_code == 429 and self.total:
            return True
        return super().is_retry(method, status_code, has_retry_after)

    def increment(self, method=None, url=None, response=None, error=None, _pool=None, _stacktrace=None):
        new = super().increment(method, url, response, error, _pool, _stacktrace)  # raises when exhausted
        cause = type(error).__name__ if error else f"HTTP {getattr(response, 'status', '?')}"
        where = f"{_pool.host}{url}" if _pool is not None and url else url
        logger.warning("Retrying %s %s after %s (%s retries left)", method, where, cause, new.total)
        return new


def _retry(s: dict) -> Retry:
    methods = set(Retry.DEFAULT_ALLOWED_METHODS) | ({"POST"} if s["retry_post"] else set())
    n = int(s["max_retries"])
    return _Retry(total=n, connect=n, read=n, status=n, other=0,
                  backoff_factor=float(s["backoff"]), status_forcelist=RETRY_STATUSES,
                  allowed_methods=frozenset(methods), respect_retry_after_header=True,
                  raise_on_status=False)


class Session(requests.Session):
    """A requests.Session that waits for its service's rate limiter and retries for it."""

    def __init__(self, service: str):
        super().__init__()
        s = settings(service)
        self.service = service
        self.default_timeout = s["timeout"]
        self._limiter = limiter(service)
        adapter = HTTPAdapter(max_retries=_retry(s))
        self.mount("https://", adapter)
        self.mount("http://", adapter)
        ua = _user_agent(service)
        if ua:
            self.headers["User-Agent"] = ua

    def request(self, method, url, *args, **kwargs):
        kwargs.setdefault("timeout", self.default_timeout)
        self._limiter.wait()
        return super().request(method, url, *args, **kwargs)


def session(service: str) -> Session:
    """The shared session for a service (cookies and connections are kept between calls)."""
    with _lock:
        if service not in _sessions:
            _sessions[service] = Session(service)
        return _sessions[service]


def new_session(service: str) -> Session:
    """A fresh session (its own cookies, e.g. FurAffinity's browser cookies) on the service's limits."""
    return Session(service)


def request(service: str, method: str, url: str, **kwargs) -> requests.Response:
    return session(service).request(method, url, **kwargs)


def get(service: str, url: str, **kwargs) -> requests.Response:
    return request(service, "GET", url, **kwargs)


def post(service: str, url: str, **kwargs) -> requests.Response:
    return request(service, "POST", url, **kwargs)


def delete(service: str, url: str, **kwargs) -> requests.Response:
    return request(service, "DELETE", url, **kwargs)


def reset():
    """Forget sessions and limiters (tests, or after changing config.yaml in-process)."""
    with _lock:
        for s in _sessions.values():
            s.close()
        _sessions.clear()
        _limiters.clear()
