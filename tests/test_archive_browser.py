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


# --- trending tags -------------------------------------------------------------

from datetime import datetime, timedelta  # noqa: E402

NOW = datetime(2026, 11, 10, 12, 0, 0)


class _Cursor:
    """MySQL-style %s placeholders and datetime params over sqlite3."""

    def __init__(self, conn):
        self._cur = conn.cursor()

    def execute(self, sql, params=()):
        params = [p.isoformat(" ") if isinstance(p, datetime) else p for p in params]
        self._cur.execute(sql.replace("%s", "?"), params)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


@pytest.fixture
def tag_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE posts (id INTEGER PRIMARY KEY, ingested_at TEXT);
        CREATE TABLE tags (tag_id INTEGER PRIMARY KEY, name TEXT, tag_type TEXT);
        CREATE TABLE posts_tags (post_id INTEGER, tag_id INTEGER);
        CREATE TABLE artists (artist_id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE posts_artists (post_id INTEGER, artist_id INTEGER);
        CREATE TABLE tag_classifications (tag_id INTEGER PRIMARY KEY, is_generic INTEGER, is_fetish INTEGER);
    """)
    old = (NOW - timedelta(days=200)).isoformat(" ")
    recent = (NOW - timedelta(days=3)).isoformat(" ")
    old_ids, recent_ids = range(1, 101), range(101, 121)  # 100 old posts, 20 recent
    conn.executemany("INSERT INTO posts VALUES (?, ?)",
                     [(i, old) for i in old_ids] + [(i, recent) for i in recent_ids])
    tags = {  # name: (type, old posts, recent posts, mark)
        "mammal": ("Species", old_ids, recent_ids, None),              # everywhere: lift 1
        "newchar": ("Character", [], recent_ids[:6], None),             # lift 6
        "oldchar": ("Character", old_ids[:10], recent_ids[:5], None),   # lift 2
        "rare": ("Character", [], recent_ids[:2], None),                # under the minimum
        "male_(lore)": ("Lore", [], recent_ids[:5], None),              # no lore list
        "solo": ("General", [], recent_ids, None),                      # general, not marked fetish
        "comic": ("Meta", [], recent_ids[:10], None),                   # meta
        "untyped": (None, [], recent_ids[:8], None),                    # counts as general
        "dragon": ("Species", [], recent_ids[:9], "generic"),           # lift 6, but generic
        "scalie": ("Species", [], recent_ids[:7], "specific"),          # reviewed: not generic
        "chastity_cage": ("General", [], recent_ids[:8], "fetish"),     # lift 6
        "hyper": ("General", old_ids[:5], recent_ids[:5], "fetish"),    # lift 3
        "bondage": ("General", [], recent_ids[:3], "fetish"),           # under the minimum
    }
    for tag_id, (name, (kind, olds, recents, mark)) in enumerate(tags.items(), 1):
        conn.execute("INSERT INTO tags VALUES (?, ?, ?)", (tag_id, name, kind))
        conn.executemany("INSERT INTO posts_tags VALUES (?, ?)", [(p, tag_id) for p in [*olds, *recents]])
        if mark:
            conn.execute("INSERT INTO tag_classifications VALUES (?, ?, ?)",
                         (tag_id, int(mark == "generic"), int(mark == "fetish")))
    artists = {"newartist": ([], recent_ids[:10]), "steady": (old_ids[:50], recent_ids[10:]),
               "oneoff": ([], recent_ids[:2])}
    for artist_id, (name, (olds, recents)) in enumerate(artists.items(), 1):
        conn.execute("INSERT INTO artists VALUES (?, ?)", (artist_id, name))
        conn.executemany("INSERT INTO posts_artists VALUES (?, ?)", [(p, artist_id) for p in [*olds, *recents]])
    yield conn
    conn.close()


def lists(result):
    return {cat["key"]: [(t["name"], t["recent"], t["lift"]) for t in cat["items"]] for cat in result["categories"]}


def test_trending_lists_rank_by_lift_per_category(tag_db):
    result = archive_browser.trending(_Cursor(tag_db), 30, now=NOW)
    assert result["recent_posts"] == 20
    assert [cat["title"] for cat in result["categories"]] == ["Artists", "Characters", "Species", "Fetish"]
    assert lists(result) == {
        "artist": [("newartist", 10, 6.0), ("steady", 10, 1.0)],
        "character": [("newchar", 6, 6.0), ("oldchar", 5, 2.0)],
        "species": [("scalie", 7, 6.0), ("mammal", 20, 1.0)],   # dragon is marked generic
        "fetish": [("chastity_cage", 8, 6.0), ("hyper", 5, 3.0)],  # only tags marked fetish
    }


def test_trending_respects_limit(tag_db):
    result = archive_browser.trending(_Cursor(tag_db), 30, now=NOW, limit=1)
    assert all(len(cat["items"]) == 1 for cat in result["categories"])


def test_trending_with_no_recent_posts_is_empty(tag_db):
    result = archive_browser.trending(_Cursor(tag_db), 7, now=NOW + timedelta(days=30))
    assert result["recent_posts"] == 0
    assert all(cat["items"] == [] for cat in result["categories"])


@pytest.fixture
def trending_calls(monkeypatch):
    calls = []

    class _Conn:
        def cursor(self):
            return self

        def close(self):
            pass

    monkeypatch.setattr(archive_browser, "_trending_cache", {})
    monkeypatch.setattr(archive_browser.generic_tags, "ensure_table", lambda conn: None)
    monkeypatch.setattr(archive_browser, "database_connection", _Conn)
    monkeypatch.setattr(archive_browser, "trending",
                        lambda cursor, days: calls.append(days) or {"days": days, "recent_posts": 1, "categories": []})
    return calls


@pytest.mark.parametrize("query, days", [("", 30), ("?days=7", 7), ("?days=90", 90), ("?days=365", 30), ("?days=x", 30)])
def test_trending_endpoint_only_allows_offered_windows(trending_calls, query, days):
    assert archive_browser.app.test_client().get(f"/api/trending{query}").get_json()["days"] == days


def test_trending_endpoint_caches_each_window(trending_calls):
    client = archive_browser.app.test_client()
    for query in ("?days=30", "?days=30", "?days=7", "?days=30"):
        client.get(f"/api/trending{query}")
    assert trending_calls == [30, 7]


def test_tag_sections_follow_e621_order_with_unknown_types_last():
    sections = ["Meta", "General", "other", "Species", "Lore", "Character", "Copyright", "Invalid"]
    assert sorted(sections, key=archive_browser.tag_section_rank) == [
        "Copyright", "Character", "Species", "General", "Lore", "Meta", "Invalid", "other"]
