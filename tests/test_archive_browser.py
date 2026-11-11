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


@pytest.fixture(autouse=True)
def _no_default_list(monkeypatch, tmp_path):
    """data/kink_tags.txt is the user's own file; tests never read it."""
    monkeypatch.setattr(archive_browser.generic_tags, "DEFAULT_FETISH_FILE", tmp_path / "missing.txt")


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
        "nintendo": ("Copyright", old_ids[:20], recent_ids[:10], None),  # lift 2
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
               "oneoff": ([], recent_ids[:2]),
               "conditional_dnp": (old_ids, recent_ids)}  # an e621 warning, not an artist
    for artist_id, (name, (olds, recents)) in enumerate(artists.items(), 1):
        conn.execute("INSERT INTO artists VALUES (?, ?)", (artist_id, name))
        conn.executemany("INSERT INTO posts_artists VALUES (?, ?)", [(p, artist_id) for p in [*olds, *recents]])
    yield conn
    conn.close()


def lists(result):
    return {cat["key"]: [(t["name"], t["recent"], t["lift"]) for t in cat["items"]] for cat in result["categories"]}


def test_rising_ranks_each_list_by_lift(tag_db):
    result = archive_browser.trending(_Cursor(tag_db), 30, now=NOW, order="rising")
    assert result["recent_posts"] == 20
    assert [cat["title"] for cat in result["categories"]] == ["Artists", "Copyrights", "Characters", "Species", "Fetish"]
    assert lists(result) == {
        "artist": [("newartist", 10, 6.0), ("steady", 10, 1.0)],
        "character": [("newchar", 6, 6.0), ("oldchar", 5, 2.0)],
        "copyright": [("nintendo", 10, 2.0)],
        "species": [("scalie", 7, 6.0), ("mammal", 20, 1.0)],   # dragon is marked generic
        "fetish": [("chastity_cage", 8, 6.0), ("hyper", 5, 3.0)],  # only tags marked fetish
    }


def test_most_posts_ranks_each_list_by_recent_posts(tag_db):
    result = archive_browser.trending(_Cursor(tag_db), 30, now=NOW)  # the default order
    assert result["order"] == "top"
    assert lists(result)["species"] == [("mammal", 20, 1.0), ("scalie", 7, 6.0)]
    # equal recent counts fall back to lift; conditional_dnp (20 recent) is not an artist
    assert lists(result)["artist"] == [("newartist", 10, 6.0), ("steady", 10, 1.0)]


def test_default_fetish_list_fills_the_fetish_trend_unless_marked(tag_db):
    # solo: no mark, so the list applies. scalie: saved "not generic", so the list doesn't.
    result = archive_browser.trending(_Cursor(tag_db), 30, now=NOW, order="rising",
                                      default_fetish=frozenset({"solo", "scalie"}))
    assert lists(result)["fetish"] == [("solo", 20, 6.0), ("chastity_cage", 8, 6.0), ("hyper", 5, 3.0)]
    assert ("scalie", 7, 6.0) in lists(result)["species"]


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
    monkeypatch.setattr(archive_browser, "trending_candidates", lambda cursor, days: calls.append(days) or {
        "days": days, "recent_posts": 10, "total_posts": 100,
        "buckets": {**{key: [] for key, _ in archive_browser.TRENDING_CATEGORIES},
                    "artist": [("a", 5, 50), ("b", 4, 4)]}})
    return calls


@pytest.mark.parametrize("query, days", [("", 30), ("?days=7", 7), ("?days=90", 90), ("?days=365", 30), ("?days=x", 30)])
def test_trending_endpoint_only_allows_offered_windows(trending_calls, query, days):
    assert archive_browser.app.test_client().get(f"/api/trending{query}").get_json()["days"] == days


def test_trending_endpoint_caches_each_window(trending_calls):
    client = archive_browser.app.test_client()
    for query in ("?days=30", "?days=30", "?days=7", "?days=30"):
        client.get(f"/api/trending{query}")
    assert trending_calls == [30, 7]


def test_trending_endpoint_orders_from_the_same_cached_query(trending_calls):
    client = archive_browser.app.test_client()

    def artists(query):
        data = client.get(f"/api/trending{query}").get_json()
        return data["order"], [t["name"] for t in data["categories"][0]["items"]]

    assert artists("") == ("top", ["a", "b"])
    assert artists("?order=rising") == ("rising", ["b", "a"])
    assert artists("?order=bogus") == ("top", ["a", "b"])
    assert trending_calls == [30]  # switching order never re-queries


def test_tag_sections_follow_e621_order_with_unknown_types_last():
    sections = ["Meta", "General", "other", "Species", "Lore", "Character", "Copyright", "Invalid"]
    assert sorted(sections, key=archive_browser.tag_section_rank) == [
        "Copyright", "Character", "Species", "General", "Lore", "Meta", "Invalid", "other"]


def test_exact_search_matches_whole_tag_or_artist_names_only():
    where, values = post_filters({"q": "macro", "exact": "1"})
    assert "LIKE" not in where
    assert "qtag.name = %s" in where and "qar.name = %s" in where
    assert values == ["macro", "macro"]


def test_typed_search_still_matches_substrings():
    where, values = post_filters({"q": "macro"})
    assert "qtag.name LIKE %s" in where
    assert values == ["%macro%"] * 4


def test_post_page_tag_links_are_exact_searches():
    html = archive_browser.DETAIL_PAGE
    assert html.count("&amp;exact=1") == 2  # artist links and tag links


# --- trends over time -------------------------------------------------------------


def overview(tag_db, category, months=12, **kwargs):
    kwargs.setdefault("default_fetish", frozenset())
    return archive_browser.trends_overview(_Cursor(tag_db), months, category, now=NOW, **kwargs)


def test_trends_put_the_first_import_day_in_its_own_column(tag_db):
    data = overview(tag_db, "species")
    # posts: 100 on the first day (the import, 200 days ago) and 20 three days ago
    assert data["before"]["posts"] == 100
    assert data["before"]["date"] == "Apr 24, 2026"
    # 12 months back would start Dec 2025, but tracking began after the Apr 24 import
    assert [m["key"] for m in data["months"]] == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
                                                  "2026-09", "2026-10", "2026-11"]
    assert [m["posts"] for m in data["months"]] == [0] * 7 + [20]
    assert [(r["name"], r["total"], r["before"], r["counts"][-1]) for r in data["rows"]] == [
        ("mammal", 20, 100, 20), ("scalie", 7, 0, 7)]  # dragon is marked generic


def test_trends_range_and_row_limit(tag_db):
    data = overview(tag_db, "species", months=3, limit=1)
    assert [m["label"] for m in data["months"]] == ["Sep 2026", "Oct 2026", "Nov 2026"]
    assert [r["name"] for r in data["rows"]] == ["mammal"]


def test_trends_follow_the_sidebar_rules_per_category(tag_db):
    names = lambda data: [(r["name"], r["total"], r["before"]) for r in data["rows"]]  # noqa: E731
    # warning tags are not artists; ties on recent posts fall back to earlier posts
    assert names(overview(tag_db, "artist")) == [("steady", 10, 50), ("newartist", 10, 0), ("oneoff", 2, 0)]
    assert names(overview(tag_db, "copyright")) == [("nintendo", 10, 20)]
    # fetish: saved fetish marks plus listed tags without a saved mark
    assert names(overview(tag_db, "fetish", default_fetish=frozenset({"solo"}))) == [
        ("solo", 20, 0), ("chastity_cage", 8, 0), ("hyper", 5, 5), ("bondage", 3, 0)]
    # a listed tag with no saved mark leaves Species for Fetish; a saved mark keeps it
    species = overview(tag_db, "species", default_fetish=frozenset({"mammal", "scalie"}))
    assert [r["name"] for r in species["rows"]] == ["scalie"]


@pytest.fixture
def trends_calls(monkeypatch):
    calls = []

    class _Conn:
        def cursor(self):
            return self

        def close(self):
            pass

    monkeypatch.setattr(archive_browser, "_trends_cache", {})
    monkeypatch.setattr(archive_browser.generic_tags, "ensure_table", lambda conn: None)
    monkeypatch.setattr(archive_browser, "database_connection", _Conn)
    monkeypatch.setattr(archive_browser, "trends_overview",
                        lambda cursor, months, category, limit: calls.append((months, category, limit)) or {})
    return calls


@pytest.mark.parametrize("query, expected", [
    ("", (12, "species", 20)),
    ("?range=3&category=fetish&rows=10", (3, "fetish", 10)),
    ("?range=24&category=general&rows=500", (12, "species", 20)),  # only the offered choices
    ("?range=x&rows=y", (12, "species", 20)),
])
def test_trends_endpoint_validates_and_caches(trends_calls, query, expected):
    client = archive_browser.app.test_client()
    assert client.get(f"/api/trends{query}").status_code == 200
    client.get(f"/api/trends{query}")
    assert trends_calls == [expected]


def test_trends_page_and_links():
    client = archive_browser.app.test_client()
    page = client.get("/trends").data.decode()
    assert '["copyright", "Copyrights"]' in page  # categories reach the page's script
    assert 'href="/trends"' in client.get("/").data.decode()
