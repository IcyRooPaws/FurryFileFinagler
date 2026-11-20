"""A cached favorites list is reused only when it is whole, and the unfavorite step refuses an
implausible number of posts (a partial list makes every missing favorite look unfavorited)."""
import json
import os
import time
from datetime import datetime, timedelta

import pytest

from fff.archive import api, sync


def stamp(minutes_ago: float) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y%m%d_%H%M%S")


def write_session(folder, session, pages, complete=True, age_files_minutes=None):
    """``pages``: list of post-id lists; an empty last page is added, as a real fetch saves one."""
    folder.mkdir(exist_ok=True)
    pages = list(pages) + [[]]
    for number, ids in enumerate(pages, 1):
        path = folder / f"temp_page_{number}_{session}.json"
        path.write_text(json.dumps({"page": number, "response": [{"id": i} for i in ids]}), encoding="utf-8")
        if age_files_minutes is not None:
            old = time.time() - age_files_minutes * 60
            os.utime(path, (old, old))
    if complete:
        api._mark_session_complete(str(folder), session, len(pages), sum(len(ids) for ids in pages))


@pytest.fixture
def cache(tmp_path):
    folder = tmp_path / "e621_pages"
    return folder, {"_temp_response_folder": str(folder)}


def ids(posts):
    return [p["id"] for p in posts]


def test_a_complete_recent_fetch_is_reused_in_page_order(cache):
    folder, config = cache
    pages = [[n * 10 + k for k in range(3)] for n in range(1, 12)]     # 11 pages: 10 sorts before 2 as text
    write_session(folder, stamp(2), pages)
    assert ids(api.load_recent_favorites_from_temp(config)) == [i for page in pages for i in page]


def test_an_unfinished_fetch_is_never_reused(cache):
    folder, config = cache
    write_session(folder, stamp(2), [[1, 2], [3, 4]], complete=False)
    assert api.load_recent_favorites_from_temp(config) is None


def test_a_fetch_that_started_too_long_ago_is_not_reused_even_if_its_last_pages_are_young(cache):
    """The bug: a sync 15-20 minutes after a fetch began saw only the pages written later."""
    folder, config = cache
    write_session(folder, stamp(17), [[1, 2], [3, 4], [5, 6]])
    assert api.load_recent_favorites_from_temp(config, max_age_minutes=15) is None


def test_a_session_missing_a_page_is_not_reused(cache):
    folder, config = cache
    session = stamp(2)
    write_session(folder, session, [[1, 2], [3, 4], [5, 6]])
    (folder / f"temp_page_2_{session}.json").unlink()      # what the per-file age cleanup used to do
    assert api.load_recent_favorites_from_temp(config) is None


def test_an_unreadable_page_or_marker_is_not_reused(cache):
    folder, config = cache
    session = stamp(2)
    write_session(folder, session, [[1, 2], [3, 4]])
    (folder / f"temp_page_1_{session}.json").write_text("{not json", encoding="utf-8")
    assert api.load_recent_favorites_from_temp(config) is None
    write_session(folder, session, [[1, 2], [3, 4]])
    (folder / f"temp_page_complete_{session}.json").write_text("{}", encoding="utf-8")
    assert api.load_recent_favorites_from_temp(config) is None


def test_the_newest_finished_fetch_wins_over_a_newer_unfinished_one(cache):
    folder, config = cache
    write_session(folder, stamp(5), [[1, 2]])
    write_session(folder, stamp(1), [[7]], complete=False)        # still being fetched, or killed
    assert ids(api.load_recent_favorites_from_temp(config)) == [1, 2]


def test_a_max_posts_cut_returns_only_the_posts_the_fetch_kept(cache):
    folder, config = cache
    session = stamp(2)
    folder.mkdir()
    (folder / f"temp_page_1_{session}.json").write_text(json.dumps({"response": [{"id": i} for i in (1, 2, 3)]}))
    api._mark_session_complete(str(folder), session, 1, 2)       # stopped at max_posts = 2 on page 1
    assert ids(api.load_recent_favorites_from_temp(config)) == [1, 2]


def test_the_cleanups_remove_the_marker_with_the_pages(cache):
    folder, config = cache
    write_session(folder, stamp(30), [[1]], age_files_minutes=30)
    old = time.time() - 30 * 60
    for path in folder.iterdir():
        os.utime(path, (old, old))
    api.cleanup_temp_responses(config, max_age_minutes=15)
    assert not folder.exists() or not list(folder.iterdir())


def test_fetch_favorites_marks_a_finished_fetch(cache, monkeypatch):
    folder, config = cache
    responses = iter([[{"id": 1}, {"id": 2}], [{"id": 3}], []])

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    monkeypatch.setattr(api, "api_get", lambda *a, **k: Response(next(responses)))
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    config.update(favorites_username="someone")
    assert ids(api.fetch_favorites(config)) == [1, 2, 3]
    assert ids(api.load_recent_favorites_from_temp(config)) == [1, 2, 3]


def test_a_fetch_that_fails_midway_leaves_nothing_reusable(cache, monkeypatch):
    folder, config = cache

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": 1}]

    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) > 2:
            raise RuntimeError("HTTP 502")
        return Response()

    monkeypatch.setattr(api, "api_get", flaky)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    config.update(favorites_username="someone")
    with pytest.raises(RuntimeError):
        api.fetch_favorites(config)
    assert len(list(folder.glob("temp_page_*"))) == 2
    assert api.load_recent_favorites_from_temp(config) is None


# --- the unfavorite step ------------------------------------------------------------------

class Conn:
    def __init__(self, ids):
        self.ids = ids

    def cursor(self):
        conn = self

        class Cursor:
            def execute(self, sql, params=()):
                self.sql = sql

            def fetchone(self):
                return (len(conn.ids),)

            def fetchall(self):
                return [(i, f"a-safe-{i}.png", "safe", None, None, 0) for i in conn.ids]

            def close(self):
                pass

        return Cursor()


@pytest.fixture
def removal(monkeypatch):
    done = []
    monkeypatch.setattr(sync, "archive_unfavorited_posts", lambda conn, posts: done.append(len(posts)) or True)
    monkeypatch.setattr(sync, "unfavorite_fa_sources", lambda *a: None)
    monkeypatch.setattr(sync, "delete_posts_from_db", lambda conn, ids: done.append(("deleted", len(ids))))
    return done


def test_an_implausible_number_of_unfavorites_removes_nothing(removal, caplog):
    conn = Conn(list(range(1, 20_001)))            # 20,000 posts: the limit is 200
    sync.handle_unfavorited_posts(conn, set(range(1, 19_000)), {})     # 1,001 "missing"
    assert removal == [] and "more than the 200 a run will remove" in caplog.text


def test_ordinary_unfavorites_go_through(removal):
    conn = Conn(list(range(1, 20_001)))
    sync.handle_unfavorited_posts(conn, set(range(1, 19_996)), {})      # 5 missing
    assert removal == [5, ("deleted", 5)]


def test_an_empty_favorites_list_removes_nothing(removal):
    sync.handle_unfavorited_posts(Conn(list(range(1, 501))), set(), {})
    assert removal == []


def test_the_limit_can_be_set(removal):
    conn = Conn(list(range(1, 501)))
    sync.handle_unfavorited_posts(conn, set(), {"max_unfavorited_per_run": 1000})
    assert removal == [500, ("deleted", 500)]
    assert sync.unfavorite_limit(Conn(list(range(30_000))), {}) == 300
    assert sync.unfavorite_limit(Conn([1]), {}) == sync.MAX_UNFAVORITED


# --- a response the sync cannot trust ------------------------------------------------------

def test_well_formed_favorites_pass():
    posts = [{"id": 1, "flags": {"deleted": False}}, {"id": 2, "flags": {"deleted": True, "pending": False}}]
    assert sync.malformed_favorites(posts) == []


def test_posts_without_a_deleted_flag_or_an_id_are_reported():
    posts = [{"id": 1, "flags": {"deleted": False}},
             {"id": 2},                                   # no flags object
             {"id": 3, "flags": None},                    # flags: null
             {"id": 4, "flags": {"pending": False}},      # no deleted key
             {"id": 5, "flags": {"deleted": "false"}},    # not a boolean
             {"flags": {"deleted": False}},               # no id
             {"id": True, "flags": {"deleted": False}},   # not an id
             "not a post"]
    assert sync.malformed_favorites(posts) == [2, 3, 4, 5, "#5", "#6", "#7"]


def test_todays_cached_favorites_are_all_well_formed():
    """The real response format, if a cache is present on this machine."""
    from fff.archive.db import _parse_post_list
    from fff.common import paths

    pages = sorted(paths.E621_PAGE_CACHE.glob("temp_page_[0-9]*.json"))
    if not pages:
        pytest.skip("no cached favorites pages")
    posts = [p for page in pages for p in _parse_post_list(json.loads(page.read_text(encoding="utf-8"))["response"])]
    assert posts and sync.malformed_favorites(posts) == []


def test_the_sync_stops_before_writing_when_favorites_are_malformed(monkeypatch, caplog):
    closed = []

    class Conn:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(sync, "load_config", lambda: {"e621": {}, "download": {}, "mysql": {}, "log": {}})
    monkeypatch.setattr(sync, "setup_logging", lambda d: None)
    monkeypatch.setattr(sync, "cleanup_temp_responses", lambda *a, **k: None)
    monkeypatch.setattr(sync, "check_and_cleanup_stale_responses", lambda *a, **k: False)
    monkeypatch.setattr(sync, "load_recent_favorites_from_temp", lambda *a, **k: None)
    monkeypatch.setattr(sync, "connect_to_mysql", lambda cfg: Conn())
    monkeypatch.setattr(sync, "create_tables_if_not_exists", lambda conn: None)
    monkeypatch.setattr(sync, "normalize_ratings", lambda conn: None)
    monkeypatch.setattr(sync, "fetch_favorites", lambda cfg: [{"id": 1, "flags": {"deleted": False}}, {"id": 2}])
    monkeypatch.setattr(sync, "get_existing_posts_info",
                        lambda conn: pytest.fail("the sync went on with a malformed favorites list"))
    with pytest.raises(SystemExit) as stopped:
        sync.main([])
    assert stopped.value.code == 1 and closed == [True]
    assert "1 of 2 favorites arrived without an id or a deleted flag (first: 2)" in caplog.text
