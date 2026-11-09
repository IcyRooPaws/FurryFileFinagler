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
    ({"post": {"id": 1, "is_favorited": True}}, True),
    ({"post": {"id": 1, "is_favorited": False}}, False),
    ({"id": 1, "files": {}}, None),            # the ?v2=true format: no is_favorited at all
])
def test_is_favorited(monkeypatch, calls, body, expected):
    def get(url, **kw):
        calls.append(url)
        return Resp(body)

    monkeypatch.setattr(fmp.requests, "get", get)
    assert fmp.check_if_favorited(1, {}) is expected
    assert "v2=true" not in calls[0]
