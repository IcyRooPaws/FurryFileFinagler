"""check_if_favorited must not read a missing is_favorited field as "not favorited"."""
import pytest

from fff.archive.scripts.favorites import favorite_missing_posts as fmp


class Resp:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(fmp.time, "sleep", lambda s: None)
    monkeypatch.setattr(fmp, "get_auth", lambda cfg: ("u", "k"))
    return seen


@pytest.mark.parametrize("body, expected", [
    ({"id": 1, "files": {}, "stats": {"is_favorited": True}}, True),     # v2: the flag is in stats
    ({"id": 1, "files": {}, "stats": {"is_favorited": False}}, False),
    ({"id": 1, "files": {}, "stats": {"fav_count": 3}}, None),          # not logged in: no flag
    ({"post": {"id": 1, "is_favorited": True}}, True),                  # v1
    ({"post": {"id": 1, "is_favorited": False}}, False),
])
def test_is_favorited(monkeypatch, calls, body, expected):
    def get(service, url, **kw):
        assert service == "e621"
        calls.append(kw.get("params"))
        return Resp(body)

    monkeypatch.setattr(fmp.http, "get", get)
    assert fmp.check_if_favorited(1, {}) is expected
    assert calls[0] == {"v2": "true", "mode": "extended"}
