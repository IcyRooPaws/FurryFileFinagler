"""Media-type filtering for the archive browser's /api/posts endpoint.

The SQL is exercised against an in-memory SQLite stand-in for posts, so the
file_type / file_path fallback logic is checked by result, not by string.
"""

import sqlite3

import pytest

pytest.importorskip("flask")

from fff.gui.archive_browser import MEDIA_TYPES, post_filters  # noqa: E402

ROWS = [
    # id, file_type, file_path
    (1, "image", r"P:\a\x-safe-1.png"),
    (2, "gif", r"P:\a\x-safe-2.gif"),
    (3, "video", r"P:\a\x-safe-3.webm"),
    # Deleted posts: file_url had no extension, so ingest said 'unknown'.
    (4, "unknown", r"P:\a\x-explicit-4.JPG"),
    (5, "unknown", r"P:\a\x-explicit-5.gif"),
    (6, "unknown", r"P:\a\x-explicit-6.mp4"),
    (7, "unknown", None),
    (8, "flash", r"P:\a\x-safe-8.swf"),
    (9, None, r"P:\a\x-safe-9.zip"),
    # A trusted category wins over a misleading extension.
    (10, "video", r"P:\a\x-safe-10.png"),
]


def _substring_index(text, delim, count):
    # Only the count = -1 form is used: everything after the last delimiter.
    return None if text is None else text.rsplit(delim, 1)[-1]


@pytest.fixture(scope="module")
def db():
    conn = sqlite3.connect(":memory:")
    conn.create_function("SUBSTRING_INDEX", 3, _substring_index)
    conn.execute("CREATE TABLE posts (id INTEGER, file_type TEXT, file_path TEXT, rating TEXT, deleted INTEGER)")
    conn.executemany("INSERT INTO posts VALUES (?, ?, ?, 'safe', 0)", ROWS)
    yield conn
    conn.close()


def matching_ids(db, args):
    where, values = post_filters(args)
    sql = f"SELECT p.id FROM posts p{where} ORDER BY p.id".replace("%s", "?")
    return [row[0] for row in db.execute(sql, values)]


@pytest.mark.parametrize("media, expected", [
    ("image", [1, 4]),
    ("gif", [2, 5]),
    ("video", [3, 6, 10]),
    ("other", [7, 8, 9]),
])
def test_media_filter_uses_file_type_then_path_extension(db, media, expected):
    assert matching_ids(db, {"media": media}) == expected


def test_media_buckets_partition_every_post(db):
    seen = sorted(i for m in (*MEDIA_TYPES, "other") for i in matching_ids(db, {"media": m}))
    assert seen == [row[0] for row in ROWS]


def test_media_is_case_insensitive(db):
    assert matching_ids(db, {"media": " Video "}) == matching_ids(db, {"media": "video"})


@pytest.mark.parametrize("media", ["", "all", "flash", "swf'; DROP TABLE posts"])
def test_blank_or_unknown_media_adds_no_filter(media):
    assert post_filters({"media": media}) == ("", [])


def test_media_combines_with_other_filters(db):
    where, values = post_filters({"rating": "safe", "media": "gif", "deleted": "0"})
    assert where.count(" AND ") >= 2 and values[0] == "safe" and values[-1] == 0
    assert matching_ids(db, {"rating": "safe", "media": "gif", "deleted": "0"}) == [2, 5]


# --- opening files locally ---------------------------------------------------

from fff.gui import archive_browser  # noqa: E402


@pytest.fixture
def opened(monkeypatch):
    calls = []
    monkeypatch.setattr(archive_browser, "post_file_path", lambda pid: r"P:\a\x-safe-1.png" if pid == 1 else None)
    monkeypatch.setattr(archive_browser, "launch", lambda path, reveal=False: calls.append((path, reveal)))
    return calls


def post_open(post_id, json=None, remote="127.0.0.1", **kwargs):
    client = archive_browser.app.test_client()
    return client.post(f"/api/open/{post_id}", json=json, environ_base={"REMOTE_ADDR": remote}, **kwargs)


@pytest.mark.parametrize("action, reveal", [("open", False), ("reveal", True)])
def test_open_launches_the_post_file(opened, action, reveal):
    assert post_open(1, {"action": action}).status_code == 200
    assert opened == [(r"P:\a\x-safe-1.png", reveal)]


def test_open_missing_file_is_404(opened):
    assert post_open(2, {"action": "open"}).status_code == 404
    assert opened == []


@pytest.mark.parametrize("kwargs, status", [
    ({"json": {"action": "delete"}}, 400),
    ({"data": "action=open", "content_type": "application/x-www-form-urlencoded"}, 400),
    ({"json": {"action": "open"}, "remote": "192.168.1.20"}, 403),
])
def test_open_rejects_bad_or_remote_requests(opened, kwargs, status):
    assert post_open(1, **kwargs).status_code == status
    assert opened == []


def test_open_is_post_only(opened):
    assert archive_browser.app.test_client().get("/api/open/1").status_code == 405


def test_pages_only_offer_open_buttons_to_local_browsers(monkeypatch):
    client = archive_browser.app.test_client()
    assert b"const canOpen = true;" in client.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}).data
    assert b"const canOpen = false;" in client.get("/", environ_base={"REMOTE_ADDR": "10.0.0.5"}).data


def test_launch_reveal_on_windows_selects_the_file(monkeypatch):
    commands = []
    monkeypatch.setattr(archive_browser.sys, "platform", "win32")
    monkeypatch.setattr(archive_browser.subprocess, "Popen", commands.append)
    archive_browser.launch(r"P:\some dir\x.png", reveal=True)
    assert commands == [r'explorer /select,"P:\some dir\x.png"']
