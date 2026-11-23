"""The reverse-search e621 step reads e621's v2 post format and asks for it.

e621 made v2 the default in Nov 2026. The old code read only v1 (``{"post": {...}}``,
``file.url``, ``is_favorited``), so every match read as "not favorited", favoriting got a 422
and the download found no file URL. Without mode=extended the tags are a flat list, which
hid every tag from the blacklist check.
"""
import copy
import json
import os
import time

import pytest

from fff.clients import e621

V2 = {"v2": "true", "mode": "extended"}

# Trimmed from a real posts/5807349.json?v2=true&mode=extended response
V2_POST = {
    "id": 5807349,
    "files": {
        "meta": {"md5": "0075836dfc5946621c951d6fe16324b9", "ext": "png", "size": 8029982},
        "original": {"width": 5000, "height": 3846,
                     "url": "https://static1.e621.net/data/00/75/0075836dfc5946621c951d6fe16324b9.png"},
    },
    "stats": {"score": {"up": 129, "down": -13, "total": 116}, "fav_count": 189, "is_favorited": True},
    "flags": {"pending": False, "flagged": False, "deleted": False},
    "rating": "e",
    "tags": {"general": ["anal", "antlers", "female"], "artist": ["arcticdunx"], "species": ["arctic_fox"]},
}

V1_POST = {"post": {
    "id": 5807349,
    "file": {"md5": "0075836dfc5946621c951d6fe16324b9", "ext": "png",
             "url": "https://static1.e621.net/data/00/75/0075836dfc5946621c951d6fe16324b9.png"},
    "is_favorited": True,
    "flags": {"deleted": False},
    "rating": "e",
    "tags": {"general": ["anal", "antlers", "female"], "artist": ["arcticdunx"], "species": ["arctic_fox"]},
}}


class Resp:
    def __init__(self, body, status_code=200, content=b""):
        self.body, self.status_code, self.content = body, status_code, content

    def json(self):
        return self.body

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192, decode_unicode=False):
        yield self.content


class Seen(list):
    """Requests made, as (service, url, params); e621 API calls are answered with ``body``."""
    body = None


@pytest.fixture
def api(monkeypatch):
    seen = Seen()

    def get(service, url, **kw):
        seen.append((service, url, kw.get("params")))
        if service == "e621_files":
            return Resp(None, content=b"image bytes")
        return Resp(seen.body)

    monkeypatch.setattr(e621.http, "get", get)
    return seen


@pytest.mark.parametrize("body", [V2_POST, V1_POST])
def test_favorite_status_is_read_from_either_format(api, body):
    api.body = body
    assert e621.check_if_favorited("5807349", "u", "k", "ua") is True
    assert api[0][2] == V2


def test_a_v2_post_that_is_not_favorited_reads_as_not_favorited(api):
    api.body = copy.deepcopy(V2_POST)
    api.body["stats"]["is_favorited"] = False
    assert e621.check_if_favorited("5807349", "u", "k", "ua") is False


def test_post_lookup_asks_for_v2_with_tag_categories(api):
    api.body = V2_POST
    assert e621.get_post_data("5807349", "u", "k", "ua") == V2_POST
    assert api == [("e621", "https://e621.net/posts/5807349.json", V2)]


def test_a_recent_cached_response_with_flat_tags_is_fetched_again(api, tmp_path):
    flat = {**V2_POST, "tags": ["anal", "antlers", "arcticdunx", "female"]}
    (tmp_path / "e621_5807349_20261123_154603.json").write_text(json.dumps(flat), encoding="utf-8")
    api.body = V2_POST
    assert e621.get_post_data("5807349", "u", "k", "ua", responses_dir=str(tmp_path)) == V2_POST
    assert len(api) == 1


def test_a_recent_cached_response_with_tag_categories_is_reused(api, tmp_path):
    cached = tmp_path / "e621_5807349_20261123_154603.json"
    cached.write_text(json.dumps(V2_POST), encoding="utf-8")
    os.utime(cached, (time.time(), time.time()))
    assert e621.get_post_data("5807349", "u", "k", "ua", responses_dir=str(tmp_path)) == V2_POST
    assert api == []


@pytest.mark.parametrize("body", [V2_POST, V1_POST])
def test_filename_comes_from_artist_rating_id_and_extension(body):
    assert e621.generate_filename(body) == "arcticdunx-explicit-5807349.png"


@pytest.mark.parametrize("body", [V2_POST, V1_POST])
def test_download_uses_the_original_file_url(api, tmp_path, body):
    path = e621.download_post(body, tmp_path, None, None, "ua")
    assert path == tmp_path / "arcticdunx-explicit-5807349.png"
    assert path.read_bytes() == b"image bytes"
    assert api[-1][:2] == ("e621_files", V2_POST["files"]["original"]["url"])


@pytest.mark.parametrize("body", [
    V2_POST,
    V1_POST,
    {**V2_POST, "tags": ["anal", "antlers", "arcticdunx", "female"]},   # flat list (mode=basic)
])
def test_blacklist_sees_the_tags(body):
    assert e621.check_blacklisted_tags(body, ["Female", "lactating"]) == ["female"]


def test_a_deleted_v2_post_without_a_file_is_recognised():
    deleted = copy.deepcopy(V2_POST)
    deleted["flags"]["deleted"] = True
    del deleted["files"]["original"]["url"]
    assert e621.is_deleted_without_file(deleted)
    assert not e621.is_deleted_without_file(V2_POST)


def test_favorites_list_reads_the_v2_bare_array(api, monkeypatch):
    monkeypatch.setattr(e621.time, "sleep", lambda s: None)
    pages = iter([[{"id": 1}, {"id": 2}], []])

    def get(service, url, **kw):
        api.append((service, url, kw.get("params")))
        return Resp(next(pages))

    monkeypatch.setattr(e621.http, "get", get)
    assert e621.get_all_favorites("u", "k", "ua") == {"1", "2"}
    assert all(params["v2"] == "true" and params["mode"] == "extended" for _, _, params in api)
