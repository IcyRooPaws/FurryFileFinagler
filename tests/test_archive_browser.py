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
