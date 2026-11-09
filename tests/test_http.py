"""The shared HTTP client: retries, which methods may repeat, rate limiting, and that all
requests go through it. A real local server is used so urllib3's retry logic actually runs."""
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from fff.clients import http
from fff.common import config as fff_config
from fff.common import paths

FAST = {"min_request_interval": 0.0, "max_retries": 3, "backoff": 0.0, "timeout": 2, "retry_post": False}


@pytest.fixture
def cfg(monkeypatch):
    """Point every service at FAST settings (tweakable per test) and start from fresh sessions."""
    conf = {"e621": {"user_agent": "FFF-test/1.0"}, "http": {name: dict(FAST) for name in http.DEFAULTS}}
    monkeypatch.setattr(fff_config, "load", lambda *a, **k: conf)
    http.reset()
    yield conf["http"]
    http.reset()


@pytest.fixture
def server():
    """A local server answering from a script: [(status, headers, delay_seconds), ...], then 200."""
    state = {"script": [], "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            state["calls"].append((self.command, self.headers.get("User-Agent")))
            status, headers, delay = state["script"].pop(0) if state["script"] else (200, {}, 0)
            if delay:
                time.sleep(delay)
            body = b"ok"
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_DELETE = _answer

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}/x"
    yield state
    srv.shutdown()


def test_429_with_retry_after_then_success(cfg, server):
    server["script"] = [(429, {"Retry-After": "0"}, 0)]
    r = http.get("e621", server["url"])
    assert r.status_code == 200 and len(server["calls"]) == 2


def test_5xx_retried_until_success(cfg, server):
    server["script"] = [(503, {}, 0), (502, {}, 0)]
    assert http.get("furaffinity", server["url"]).status_code == 200
    assert len(server["calls"]) == 3


def test_retries_running_out_return_the_last_response(cfg, server):
    cfg["e621"]["max_retries"] = 2
    server["script"] = [(500, {}, 0)] * 5
    r = http.get("e621", server["url"])
    assert r.status_code == 500 and len(server["calls"]) == 3   # first try + 2 retries


def test_read_timeout_is_retried_for_get(cfg, server):
    cfg["e621"]["timeout"] = 0.3
    server["script"] = [(200, {}, 0.8)]
    assert http.get("e621", server["url"]).status_code == 200
    assert len(server["calls"]) == 2


def test_connection_error_is_retried_then_raised(cfg):
    cfg["e621"]["max_retries"] = 1  # a refused connection takes ~2 s per try on Windows
    with socket.socket() as s:      # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(requests.ConnectionError):
        http.get("e621", f"http://127.0.0.1:{port}/x")


def test_post_is_not_repeated_after_a_5xx(cfg, server):
    """The server may have acted on it; an e621 vote sent twice would undo itself."""
    server["script"] = [(500, {}, 0)]
    assert http.post("e621", server["url"], data={"score": -1}).status_code == 500
    assert len(server["calls"]) == 1


def test_post_is_repeated_after_a_429(cfg, server):
    server["script"] = [(429, {"Retry-After": "0"}, 0)]
    assert http.post("e621", server["url"], data={"score": -1}).status_code == 200
    assert [c[0] for c in server["calls"]] == ["POST", "POST"]


def test_search_post_retries_on_5xx(cfg, server):
    cfg["fuzzysearch"]["retry_post"] = True
    server["script"] = [(503, {}, 0)]
    assert http.post("fuzzysearch", server["url"]).status_code == 200
    assert len(server["calls"]) == 2


def test_delete_is_retried(cfg, server):
    server["script"] = [(502, {}, 0)]
    assert http.delete("e621", server["url"]).status_code == 200
    assert len(server["calls"]) == 2


def test_e621_user_agent_from_config_and_caller_override(cfg, server):
    http.get("e621", server["url"])
    http.get("e621", server["url"], headers={"User-Agent": "caller/2"})
    http.get("fuzzysearch", server["url"])
    assert [c[1] for c in server["calls"]][:2] == ["FFF-test/1.0", "caller/2"]
    assert server["calls"][2][1] != "FFF-test/1.0"


def test_rate_limit_spacing_is_shared_across_threads(cfg, server):
    cfg["e621"]["min_request_interval"] = 0.2
    start = time.monotonic()
    threads = [threading.Thread(target=http.get, args=("e621", server["url"])) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - start >= 0.6   # 4 requests, 3 gaps of 0.2 s
    assert len(server["calls"]) == 4


def test_pause_every_n_requests(monkeypatch):
    slept = []
    monkeypatch.setattr(http.time, "sleep", slept.append)
    limiter = http.RateLimiter(0, pause_every=3, pause_seconds=60, name="e621")
    for _ in range(7):
        limiter.wait()
    assert slept == [60, 60]


def test_defaults_without_a_config(monkeypatch):
    def missing(*a, **k):
        raise fff_config.ConfigError("no config")

    monkeypatch.setattr(fff_config, "load", missing)
    assert http.settings("e621")["min_request_interval"] == 1.0
    with pytest.raises(ValueError):
        http.settings("nope")


def test_new_session_keeps_its_own_cookies(cfg, server):
    a, b = http.new_session("furaffinity"), http.new_session("furaffinity")
    a.cookies.set("a", "1")
    assert "a" not in b.cookies and a._limiter is b._limiter


ALLOWED = {"fff/clients/http.py"}
DIRECT = re.compile(r"\brequests\.(get|post|put|delete|patch|head|request|Session)\(")


def test_no_requests_outside_the_http_client():
    offenders = []
    for path in sorted((paths.ROOT / "fff").rglob("*.py")):
        rel = path.relative_to(paths.ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DIRECT.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "use fff.clients.http instead:\n" + "\n".join(offenders)


def test_client_never_shadows_the_stdlib_http_package():
    """'from fff.clients import http' in a module that also uses stdlib http (http.cookiejar)
    breaks http.cookiejar there; such modules must import the client under another name."""
    bad = []
    for path in sorted((paths.ROOT / "fff").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(import http\b|from http\b)", text, re.M) and \
                re.search(r"^\s*from fff\.clients import http\s*(#.*)?$", text, re.M):
            bad.append(path.relative_to(paths.ROOT).as_posix())
    assert not bad, bad
