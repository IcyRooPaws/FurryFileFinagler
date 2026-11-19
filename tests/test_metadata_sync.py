"""Posts whose file is already on disk follow e621: rating, sources, pools, relationships, a
restored post, tag categories, and a file e621 has replaced (fff.archive.metadata_sync)."""
import hashlib
import logging
import os
from datetime import datetime

import pytest

from fff.archive import metadata_sync as ms
from fff.archive import post_files


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class FakeDb:
    """The handful of statements metadata_sync runs, against plain Python tables."""

    def __init__(self):
        self.posts = {}             # id -> dict of columns
        self.sources = set()        # (post_id, link)
        self.posts_pools = set()    # (post_id, pool_id)
        self.pools = set()
        self.relations = []         # (post_id, parent_id, child_id); a list, as the table allows duplicates
        self.tags = {}              # tag_id -> [name, tag_type]
        self.commits = self.rollbacks = 0
        self.fail_on = None         # statement prefix that raises
        self.forgotten_hashes = []  # post ids whose cached e621 perceptual hash was dropped

    def add_post(self, post_id, **columns):
        row = {"rating": "safe", "deleted": 0, "md5": "m" * 32, "file_url": f"https://static/{post_id}.png",
               "updated_at": datetime(2026, 1, 1, 12, 0, 0), "deleted_at": None, "deletion_reason": None,
               "lastChecked": None, "file_path": None, "file_name": None, "media_hash": "old-hash"}
        row.update(columns)
        self.posts[post_id] = row

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows = db, []

    def execute(self, sql, params=()):
        db, p = self.db, list(params)
        if db.fail_on and sql.startswith(db.fail_on):
            raise RuntimeError(f"refused: {db.fail_on}")
        if sql.startswith("SELECT id, rating, md5, file_url, updated_at, (deleted = 1 OR deleted_at IS NOT NULL"):
            self.rows = [(i, r["rating"], r["md5"], r["file_url"], r["updated_at"],
                          int(bool(r["deleted"] or r["deleted_at"] or r["deletion_reason"])))
                         for i, r in ((i, db.posts[i]) for i in p if i in db.posts)]
        elif sql.startswith("SELECT id FROM posts WHERE id IN") and "deleted = 1 OR deleted_at IS NOT NULL" in sql:
            self.rows = [(i,) for i in p if i in db.posts and (
                db.posts[i]["deleted"] or db.posts[i]["deleted_at"] or db.posts[i]["deletion_reason"])]
        elif sql.startswith("SELECT post_id, source_link FROM source_links"):
            self.rows = [row for row in db.sources if row[0] in p]
        elif sql.startswith("SELECT post_id, pool_id FROM posts_pools"):
            self.rows = [row for row in db.posts_pools if row[0] in p]
        elif sql.startswith("SELECT post_id, parent_id, child_id FROM post_relationships"):
            self.rows = [row for row in db.relations if row[0] in p]
        elif sql.startswith("SELECT tag_id, name, tag_type FROM tags"):
            self.rows = [(i, *v) for i, v in db.tags.items()]
        elif sql.startswith("SELECT file_path, file_name FROM posts WHERE id = %s"):
            self.rows = [(db.posts[p[0]]["file_path"], db.posts[p[0]]["file_name"])] if p[0] in db.posts else []
        elif sql.startswith("SELECT COUNT(*) FROM posts WHERE file_path = %s AND id <> %s"):
            self.rows = [(sum(1 for i, r in db.posts.items() if r["file_path"] == p[0] and i != p[1]),)]
        elif sql.startswith("UPDATE posts SET rating = %s WHERE id = %s"):
            db.posts[p[1]]["rating"] = p[0]
        elif sql.startswith("UPDATE posts SET deleted = 0, deleted_at = NULL, deletion_reason = NULL WHERE id = %s"):
            db.posts[p[0]].update(deleted=0, deleted_at=None, deletion_reason=None)
        elif sql.startswith("UPDATE posts SET md5 = %s WHERE id = %s"):
            db.posts[p[1]]["md5"] = p[0]
        elif sql.startswith("UPDATE posts SET file_url = %s WHERE id = %s"):
            db.posts[p[1]]["file_url"] = p[0]
        elif sql.startswith("UPDATE posts SET updated_at = %s WHERE id = %s"):
            db.posts[p[1]]["updated_at"] = p[0]
        elif sql.startswith("UPDATE posts SET lastChecked = %s WHERE id IN"):
            for post_id in p[1:]:
                db.posts[post_id]["lastChecked"] = p[0]
        elif sql.startswith("UPDATE posts SET file_path = %s, file_name = %s, md5 = %s, "
                            "file_url = COALESCE(%s, file_url), media_hash = NULL WHERE id = %s"):
            db.posts[p[4]].update(file_path=p[0], file_name=p[1], md5=p[2], media_hash=None,
                                  file_url=p[3] or db.posts[p[4]]["file_url"])
        elif sql.startswith("UPDATE posts SET file_path = %s, file_name = %s, md5 = %s, file_url = %s, "
                            "media_hash = NULL WHERE id = %s"):
            db.posts[p[4]].update(file_path=p[0], file_name=p[1], md5=p[2], file_url=p[3], media_hash=None)
        elif sql.startswith("INSERT IGNORE INTO source_links (post_id, source_link)"):
            db.sources.add((p[0], p[1]))
        elif sql.startswith("INSERT IGNORE INTO pools (pool_id)"):
            db.pools.add(p[0])
        elif sql.startswith("INSERT IGNORE INTO posts_pools (post_id, pool_id)"):
            db.posts_pools.add((p[0], p[1]))
        elif sql.startswith("DELETE FROM posts_pools WHERE post_id = %s AND pool_id = %s"):
            db.posts_pools.discard((p[0], p[1]))
        elif sql.startswith("DELETE FROM post_relationships WHERE post_id IN"):
            db.relations = [row for row in db.relations if row[0] not in p]
        elif sql.startswith("INSERT INTO post_relationships (post_id, parent_id, child_id)"):
            db.relations.append(tuple(p))
        elif sql.startswith("UPDATE tags SET tag_type = %s WHERE tag_id = %s"):
            db.tags[p[1]][1] = p[0]
        elif sql.startswith("DELETE FROM e621_perceptual_hashes WHERE post_id = %s"):
            db.forgotten_hashes.append(p[0])
        else:
            raise AssertionError(f"unexpected statement: {sql}")

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


def api_post(post_id, **fields):
    post = {"id": post_id, "rating": "s", "flags": {"deleted": False},
            "files": {"meta": {"md5": "m" * 32, "ext": "png"}, "original": {"url": f"https://static/{post_id}.png"}},
            "updated_at": "2026-01-01T12:00:00.000+00:00", "sources": [], "pools": [],
            "relationships": {"parent_id": None, "children": []},
            "tags": {"general": ["solo"], "artist": ["someone"]}}
    post.update(fields)
    return post


@pytest.fixture
def db():
    return FakeDb()


def local(value):
    return ms._local_time(value)


CHANGE_COUNTS = ("rating", "restored", "md5", "file_url", "updated_at", "sources", "pools", "relationships")


# --- the posts row and its links ------------------------------------------------------------

def test_an_unchanged_post_is_left_alone(db):
    db.add_post(1, updated_at=local("2026-01-01T12:00:00.000+00:00"))
    stats = ms.sync_changed_post_metadata(db, [api_post(1)])
    assert stats["compared"] == 1 and not any(stats[k] for k in CHANGE_COUNTS)
    assert stats["file_changed"] == [] and stats["new_pool_ids"] == set()


def test_a_rerated_post_gets_the_new_rating(db):
    db.add_post(2, rating="safe")
    stats = ms.sync_changed_post_metadata(db, [api_post(2, rating="e")])
    assert db.posts[2]["rating"] == "explicit" and stats["rating"] == 1


def test_a_restored_post_is_no_longer_deleted(db, caplog):
    db.add_post(3, deleted=1, deleted_at=datetime(2026, 2, 2), deletion_reason="takedown",
                file_url="https://e621.net/posts/3")
    db.sources = {(3, "https://e621.net/posts/3"), (3, "https://example.com/a")}
    with caplog.at_level(logging.INFO):
        stats = ms.sync_changed_post_metadata(db, [api_post(3, sources=["https://example.com/a"])])
    assert stats["restored"] == 1 and stats["restored_ids"] == [3]
    assert "No longer deleted on e621 (deleted flag, date and reason cleared): 3" in caplog.text
    assert db.posts[3]["deleted"] == 0 and db.posts[3]["deleted_at"] is None and db.posts[3]["deletion_reason"] is None
    assert db.posts[3]["file_url"] == "https://static/3.png"
    assert len(db.sources) == 2        # source links are never removed


def test_a_post_the_full_ingest_already_undeleted_loses_its_old_date_and_reason(db):
    db.add_post(4, deleted=0, deleted_at=datetime(2026, 2, 2), deletion_reason="Inferior version")
    assert ms.sync_changed_post_metadata(db, [api_post(4)])["restored"] == 1
    assert db.posts[4]["deleted_at"] is None and db.posts[4]["deletion_reason"] is None
    assert ms.sync_changed_post_metadata(db, [api_post(4)])["restored"] == 0


def test_a_response_without_flags_never_restores(db):
    db.add_post(5, deleted=1)
    post = api_post(5)
    del post["flags"]
    assert ms.sync_changed_post_metadata(db, [post])["restored"] == 0 and db.posts[5]["deleted"] == 1
    assert ms.clear_restored_posts(db, [post]) == [] and db.posts[5]["deleted"] == 1


def test_posts_the_full_ingest_rewrote_are_checked_too(db, caplog):
    """A restored post whose file was missing: the ingest cleared the flag and kept the rest."""
    db.add_post(50, deleted=0, deleted_at=datetime(2026, 2, 2), deletion_reason="Artist requested removal")
    db.add_post(51, deleted=1, deleted_at=datetime(2026, 3, 3), deletion_reason="Inferior version")
    db.add_post(52)
    posts = [api_post(50), api_post(51, flags={"deleted": True}), api_post(52), api_post(53)]
    with caplog.at_level(logging.INFO):
        assert ms.clear_restored_posts(db, posts) == [50]
    assert db.posts[50]["deleted_at"] is None and db.posts[50]["deletion_reason"] is None
    assert db.posts[51]["deleted"] == 1 and db.posts[51]["deletion_reason"] == "Inferior version"   # still deleted
    assert "No longer deleted on e621 (deleted flag, date and reason cleared): 50" in caplog.text
    assert ms.clear_restored_posts(db, posts) == []


def test_a_restored_post_loses_its_placeholder_url_even_when_its_file_also_changed(db):
    db.add_post(54, deleted=1, md5="a" * 32, file_url="https://e621.net/posts/54")
    post = api_post(54)
    post["files"] = {"meta": {"md5": "b" * 32, "ext": "png"}, "original": {"url": "https://static/54.png"}}
    stats = ms.sync_changed_post_metadata(db, [post])
    assert stats["file_changed"] == [post] and stats["restored_ids"] == [54]
    assert db.posts[54]["file_url"] == "https://static/54.png" and db.posts[54]["md5"] == "a" * 32


def test_new_sources_are_added_and_removed_ones_are_kept(db):
    db.add_post(6)
    db.sources = {(6, "https://old.example/1"), (6, "https://Kept.example/2")}
    post = api_post(6, sources=["https://kept.example/2", "https://new.example/3", "", None])
    assert ms.sync_changed_post_metadata(db, [post])["sources"] == 1
    # e621 no longer lists old.example: the link stays. The same link in another case is the
    # same row (the table's key ignores case): not added twice.
    assert db.sources == {(6, "https://old.example/1"), (6, "https://Kept.example/2"), (6, "https://new.example/3")}
    assert ms.sync_changed_post_metadata(db, [post])["sources"] == 0   # and it settles


def test_a_post_e621_lists_no_sources_for_keeps_its_links(db):
    db.add_post(60)
    db.sources = {(60, "https://old.example/1")}
    assert ms.sync_changed_post_metadata(db, [api_post(60, sources=[])])["sources"] == 0
    assert db.sources == {(60, "https://old.example/1")}


def test_links_sharing_the_first_255_characters_settle(db):
    db.add_post(7)
    long_a, long_b = "https://x.example/" + "a" * 300 + "/1", "https://x.example/" + "a" * 300 + "/2"
    post = api_post(7, sources=[long_a, long_b])     # one row in MySQL: the key is the first 255 characters
    ms.sync_changed_post_metadata(db, [post])
    assert db.sources == {(7, long_a)}
    assert ms.sync_changed_post_metadata(db, [post])["sources"] == 0


def test_pools_follow_e621_and_new_pools_are_reported(db):
    db.add_post(8)
    db.posts_pools, db.pools = {(8, 10), (8, 11)}, {10, 11}
    stats = ms.sync_changed_post_metadata(db, [api_post(8, pools=[11, 12])])
    assert db.posts_pools == {(8, 11), (8, 12)} and 12 in db.pools
    assert stats["pools"] == 1 and stats["new_pool_ids"] == {12}


def test_relationships_follow_e621(db):
    db.add_post(9)
    db.add_post(90)
    db.relations = [(9, None, 80), (90, 1, None)]
    post = api_post(9, relationships={"parent_id": 7, "children": [81]})
    assert ms.sync_changed_post_metadata(db, [post, api_post(90, relationships={"parent_id": 1, "children": []})]
                                         )["relationships"] == 1
    assert sorted(db.relations, key=str) == sorted([(9, 7, None), (9, None, 81), (90, 1, None)], key=str)
    assert ms.sync_changed_post_metadata(db, [post])["relationships"] == 0
    assert len(db.relations) == 3      # replaced as a set: no duplicate rows


def test_a_post_listed_twice_is_handled_once(db):
    """A favorite added mid-fetch puts one post on two pages; its rows must not double."""
    db.add_post(91)
    post = api_post(91, relationships={"parent_id": 7, "children": [81]}, pools=[3])
    stats = ms.sync_changed_post_metadata(db, [post, dict(post)])
    assert stats["compared"] == 1 and stats["relationships"] == 1
    assert sorted(db.relations, key=str) == sorted([(91, 7, None), (91, None, 81)], key=str)


def test_half_a_relationships_object_is_not_no_relationships(db):
    db.add_post(92)
    db.relations = [(92, 7, None), (92, None, 81)]
    for half in ({"parent_id": 7}, {"children": []}, {"parent_id": 7, "children": None}):
        assert ms.sync_changed_post_metadata(db, [api_post(92, relationships=half)])["relationships"] == 0
    assert len(db.relations) == 2


def test_a_response_without_a_list_never_prunes(db):
    """A missing field is not 'e621 has none': the same rule the tag refresh follows."""
    db.add_post(10)
    db.sources, db.posts_pools, db.relations = {(10, "https://a.example")}, {(10, 1)}, [(10, 5, None)]
    post = api_post(10)
    for field in ("sources", "pools", "relationships"):
        del post[field]
    stats = ms.sync_changed_post_metadata(db, [post])
    assert not (stats["sources"] or stats["pools"] or stats["relationships"])
    assert db.sources and db.posts_pools and db.relations


def test_an_implausible_number_of_emptied_posts_is_left_alone(db, caplog):
    """Every post reporting no pools/relationships is a broken response, not e621."""
    count = ms.MAX_EMPTIED + 1
    for post_id in range(100, 100 + count):
        db.add_post(post_id)
        db.sources.add((post_id, "https://a.example"))
        db.posts_pools.add((post_id, 1))
        db.relations.append((post_id, 5, None))
    db.add_post(99)
    db.sources.add((99, "https://old.example"))
    posts = [api_post(post_id) for post_id in range(100, 100 + count)]
    posts.append(api_post(99, sources=["https://new.example"]))     # an ordinary change still goes through
    stats = ms.sync_changed_post_metadata(db, posts)
    assert len(db.sources) == count + 2 and (99, "https://new.example") in db.sources
    assert len(db.posts_pools) == count and len(db.relations) == count
    assert (stats["sources"], stats["pools"], stats["relationships"]) == (1, 0, 0)
    assert "e621 lists no pools for" in caplog.text and "e621 lists no relationships for" in caplog.text

    # At or under the limit it is believed.
    stats = ms.sync_changed_post_metadata(db, posts[:ms.MAX_EMPTIED])
    assert stats["pools"] == ms.MAX_EMPTIED and len(db.posts_pools) == 1 and len(db.relations) == 1
    assert len(db.sources) == count + 2          # sources are never removed, whatever the response says


def test_updated_at_and_file_url_follow_e621(db):
    db.add_post(11)
    post = api_post(11, updated_at="2026-11-19T16:46:04.291Z")
    post["files"]["original"]["url"] = "https://static2/11.png"
    stats = ms.sync_changed_post_metadata(db, [post])
    assert db.posts[11]["updated_at"] == local("2026-11-19T16:46:04.291Z") and stats["updated_at"] == 1
    assert db.posts[11]["updated_at"].microsecond == 0 and db.posts[11]["updated_at"].tzinfo is None
    assert db.posts[11]["file_url"] == "https://static2/11.png" and stats["file_url"] == 1


def test_a_changed_md5_is_reported_not_written(db):
    db.add_post(12, md5="a" * 32)
    post = api_post(12)
    post["files"]["meta"]["md5"] = "B" * 32
    post["files"]["original"]["url"] = "https://static/new.png"
    stats = ms.sync_changed_post_metadata(db, [post])
    assert stats["file_changed"] == [post]
    assert db.posts[12]["md5"] == "a" * 32 and db.posts[12]["file_url"] == "https://static/12.png"


def test_a_missing_stored_md5_is_filled_in(db):
    db.add_post(13, md5=None)
    stats = ms.sync_changed_post_metadata(db, [api_post(13)])
    assert db.posts[13]["md5"] == "m" * 32 and stats["md5"] == 1 and stats["file_changed"] == []


def test_posts_not_in_the_database_are_skipped(db):
    assert ms.sync_changed_post_metadata(db, [api_post(14), {"no": "id"}])["compared"] == 0


def test_v1_responses_are_read_too(db):
    db.add_post(15, md5="a" * 32)
    post = api_post(15)
    post["file"] = {"md5": "c" * 32, "ext": "png", "url": "https://static/15.png"}
    del post["files"]
    assert ms.sync_changed_post_metadata(db, [post])["file_changed"] == [post]


def test_nothing_is_written_when_a_statement_fails(db):
    """One transaction: an error leaves the caller to roll back, and no change is reported."""
    db.add_post(16, rating="safe")
    db.fail_on = "INSERT INTO post_relationships"
    with pytest.raises(RuntimeError):
        ms.sync_changed_post_metadata(db, [api_post(16, rating="e", relationships={"parent_id": 1, "children": []})])
    assert db.commits == 0


def test_last_checked_is_stamped_only_for_posts_that_carried_tags(db):
    db.add_post(17)
    db.add_post(18)
    when = datetime(2026, 11, 19, 13, 27, 4)
    assert ms.stamp_last_checked(db, [api_post(17), api_post(18, tags=["flat", "list"])], when) == 1
    assert db.posts[17]["lastChecked"] == "2026-11-19 13:27:04" and db.posts[18]["lastChecked"] is None


def test_tag_categories_follow_e621(db):
    db.tags = {1: ["solo", "General"], 2: ["pokemon", "General"], 3: ["gone_tag", "Species"], 4: ["someone", "General"]}
    posts = [api_post(19, tags={"general": ["solo"], "copyright": ["pokemon"], "artist": ["someone"]}),
             api_post(20, tags=["flat"])]
    assert ms.sync_tag_types(db, posts) == 1
    assert db.tags == {1: ["solo", "General"], 2: ["pokemon", "Copyright"], 3: ["gone_tag", "Species"],
                       4: ["someone", "General"]}
    assert ms.sync_tag_types(db, posts) == 0


# --- a file e621 has replaced ----------------------------------------------------------------

OLD, NEW = b"old bytes", b"new bytes from e621"
ON = {"redownload_replaced_files": True}


@pytest.fixture
def library(db, tmp_path, monkeypatch):
    """One post (20) whose local file is the version e621 has since replaced."""
    old = tmp_path / "Fav" / "Characters" / "artist-safe-20.jpg"
    old.parent.mkdir(parents=True)
    old.write_bytes(OLD)
    os.utime(old, (1_600_000_000, 1_600_000_000))
    db.add_post(20, md5=md5(OLD), file_path=str(old), file_name=old.name)
    post = api_post(20)
    post["files"] = {"meta": {"md5": md5(NEW), "ext": "png"}, "original": {"url": "https://static/new20.png"}}
    held = tmp_path / "held"
    state = {"downloads": [], "refreshed": [], "copies": [old], "locked": set(), "logged": [], "payload": NEW}

    def download(url, target, headers):
        state["downloads"].append(url)
        target.write_bytes(state["payload"])

    def move_post_files(conn, post_ids, destination, reason, dry_run=False, keep=()):
        """Like the real one: each copy moves on its own, and one failing does not stop the rest."""
        kept = {ms._key(path) for path in keep}
        moved, failed = 0, 0
        for path in state["copies"]:
            if ms._key(path) in kept or not path.exists():
                continue
            if path in state["locked"]:
                failed += 1
                continue
            held.mkdir(exist_ok=True)
            path.rename(held / path.name)
            moved += 1
        state["reason"] = reason
        return {"moved": moved, "failed": failed, "cleared": set() if failed else set(post_ids)}

    class Ops:
        def log_move_operation(self, **kw):
            state["logged"].append(kw)

    monkeypatch.setattr(ms, "_download", download)
    monkeypatch.setattr(ms.post_files, "move_post_files", move_post_files)
    monkeypatch.setattr(ms.post_files, "find_post_files",
                        lambda conn, ids: {20: [(str(path), "favorites") for path in state["copies"] if path.exists()]})
    monkeypatch.setattr(ms.post_files, "_operation_logger", lambda: Ops())
    monkeypatch.setattr(ms.post_files, "record_rename",
                        lambda conn, old, new, reason: state.setdefault("renames", []).append((old, new, reason)))
    monkeypatch.setattr(ms.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(ms, "insert_or_update_posts_bulk", lambda conn, posts: state["refreshed"].extend(posts))
    monkeypatch.setattr(ms.archive_paths, "replaced_directory", lambda: str(held))
    monkeypatch.setattr(ms.archive_paths, "TEMP_DOWNLOADS", tmp_path / "temp", raising=False)
    return {"old": old, "new": old.with_suffix(".png"), "post": post, "state": state, "root": tmp_path, "held": held}


def unchanged(db, library):
    """The post is exactly as it was: old file in place, nothing staged, row untouched."""
    folder = sorted(p.name for p in library["old"].parent.iterdir())
    return (library["old"].read_bytes() == OLD and folder == [library["old"].name]
            and db.posts[20]["md5"] == md5(OLD) and db.posts[20]["file_path"] == str(library["old"])
            and not list((library["root"] / "temp").glob("*")))


def test_a_replaced_file_is_swapped_in_place(db, library):
    stats = ms.replace_changed_files(db, [library["post"]], ON)
    assert stats == {"candidates": 1, "replaced": 1, "md5_refreshed": 0, "skipped": 0, "failed": 0}
    assert library["new"].read_bytes() == NEW                        # same folder, e621's extension
    assert sorted(p.name for p in library["new"].parent.iterdir()) == ["artist-safe-20.png"]
    assert (library["held"] / "artist-safe-20.jpg").read_bytes() == OLD          # old copy kept
    assert library["new"].stat().st_mtime == 1_600_000_000           # keeps its place in the archive's ageing
    row = db.posts[20]
    assert row["md5"] == md5(NEW) and row["file_url"] == "https://static/new20.png"
    assert row["file_name"] == "artist-safe-20.png" and row["media_hash"] is None
    assert row["file_path"] == ms.normalize_file_path(str(library["new"]))
    state = library["state"]
    assert state["reason"] == "replaced_on_e621" and state["refreshed"] == [library["post"]]
    assert state["logged"][0]["reason"] == "replaced_on_e621:new_file"
    assert state["logged"][0]["destination_path"] == str(library["new"])
    assert not list((library["root"] / "temp").iterdir())


def test_a_file_that_already_is_e621s_only_gets_its_row_corrected(db, library):
    library["old"].write_bytes(NEW)
    library["post"]["files"]["meta"]["ext"] = "jpg"
    stats = ms.replace_changed_files(db, [library["post"]], ON)
    assert stats["md5_refreshed"] == 1 and stats["replaced"] == 0
    assert library["state"]["downloads"] == [] and library["old"].exists() and not list(library["held"].iterdir())
    row = db.posts[20]
    assert row["md5"] == md5(NEW) and row["file_url"] == "https://static/new20.png" and row["media_hash"] is None
    assert row["file_name"] == "artist-safe-20.jpg" and row["file_path"] == ms.normalize_file_path(str(library["old"]))
    assert db.forgotten_hashes == [20] and "renames" not in library["state"]
    assert library["state"]["refreshed"] == [library["post"]]      # file type and the rest follow the ingest


def test_e621s_content_under_the_old_extension_is_renamed(db, library, monkeypatch):
    """Six real posts: a .jpg on disk holding the png e621 replaced it with."""
    library["old"].write_bytes(NEW)
    assert ms.replace_changed_files(db, [library["post"]], ON)["md5_refreshed"] == 1
    assert library["new"].read_bytes() == NEW and not library["old"].exists()
    row = db.posts[20]
    assert row["file_name"] == "artist-safe-20.png" and row["file_path"] == ms.normalize_file_path(str(library["new"]))
    assert library["state"]["renames"] == [(str(library["old"]), str(library["new"]), "replaced_on_e621:extension")]


def test_the_extension_is_left_alone_when_the_new_name_is_taken_or_protected(db, library, monkeypatch):
    library["old"].write_bytes(NEW)
    library["new"].write_bytes(b"someone else")
    assert ms.replace_changed_files(db, [library["post"]], ON)["md5_refreshed"] == 1
    assert library["old"].read_bytes() == NEW and library["new"].read_bytes() == b"someone else"
    library["new"].unlink()
    db.posts[20]["md5"] = md5(OLD)
    monkeypatch.setattr(ms.protected, "is_protected", lambda path: True)
    assert ms.replace_changed_files(db, [library["post"]], ON)["md5_refreshed"] == 1
    assert library["old"].exists() and db.posts[20]["file_name"] == "artist-safe-20.jpg"


def test_a_file_in_use_is_left_for_the_next_run_without_downloading(db, library, monkeypatch):
    monkeypatch.setattr(ms, "_movable", lambda path: False)
    assert ms.replace_changed_files(db, [library["post"]], ON)["skipped"] == 1
    assert unchanged(db, library) and library["state"]["downloads"] == []


def test_another_copy_that_already_is_e621s_is_kept_and_only_the_old_one_moves(db, library):
    """Post 3345593 in the real library: the archive holds the old jpg, the library the current png."""
    current = library["root"] / "NSFW Pics" / "artist-safe-20.png"
    current.parent.mkdir()
    current.write_bytes(NEW)
    library["state"]["copies"].append(current)
    stats = ms.replace_changed_files(db, [library["post"]], ON)
    assert stats["md5_refreshed"] == 1 and library["state"]["downloads"] == []
    assert current.read_bytes() == NEW                                # the good copy stays in the library
    assert not library["old"].exists() and (library["held"] / "artist-safe-20.jpg").read_bytes() == OLD
    row = db.posts[20]
    assert row["file_path"] == ms.normalize_file_path(str(current)) and row["file_name"] == "artist-safe-20.png"
    assert row["md5"] == md5(NEW)


def test_a_download_that_does_not_match_e621s_md5_changes_nothing(db, library):
    library["state"]["payload"] = b"truncated"
    assert ms.replace_changed_files(db, [library["post"]], ON)["failed"] == 1
    assert unchanged(db, library) and not list(library["held"].iterdir())


def test_a_failed_download_changes_nothing(db, library, monkeypatch):
    def boom(url, target, headers):
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(ms, "_download", boom)
    assert ms.replace_changed_files(db, [library["post"]], ON)["failed"] == 1
    assert unchanged(db, library) and db.rollbacks == 1


def test_a_failed_copy_into_the_library_changes_nothing(db, library, monkeypatch):
    """The copy across drives happens while the old file is still in its folder."""
    def boom(src, dst):
        raise OSError("drive not ready")

    monkeypatch.setattr(ms.shutil, "move", boom)
    assert ms.replace_changed_files(db, [library["post"]], ON)["failed"] == 1
    assert unchanged(db, library) and not list(library["held"].iterdir())


def test_an_old_file_that_cannot_move_stops_the_swap(db, library):
    library["state"]["locked"].add(library["old"])
    assert ms.replace_changed_files(db, [library["post"]], ON)["failed"] == 1
    assert unchanged(db, library)       # including: the staged new file is gone again


def test_a_second_old_copy_that_cannot_move_does_not_stop_the_swap(db, library, caplog):
    other = library["root"] / "Archive" / "artist-safe-20.jpg"
    other.parent.mkdir()
    other.write_bytes(OLD)
    library["state"]["copies"].append(other)
    library["state"]["locked"].add(other)
    assert ms.replace_changed_files(db, [library["post"]], ON)["replaced"] == 1
    assert library["new"].read_bytes() == NEW and not library["old"].exists() and other.exists()
    assert "an older copy elsewhere in the library could not be moved" in caplog.text


def test_a_failed_final_rename_is_reported_with_where_the_old_file_went(db, library, monkeypatch, caplog):
    rename = os.rename

    def boom(src, dst):
        if str(src).endswith(ms.STAGED_SUFFIX):
            raise OSError("sharing violation")
        return rename(src, dst)

    attempts = []
    monkeypatch.setattr(ms.os, "rename", lambda src, dst: (attempts.append(src), boom(src, dst))[1])
    assert ms.replace_changed_files(db, [library["post"]], ON)["failed"] == 1
    assert sum(1 for src in attempts if str(src).endswith(ms.STAGED_SUFFIX)) == ms.RENAME_ATTEMPTS
    assert (library["held"] / "artist-safe-20.jpg").read_bytes() == OLD
    assert db.posts[20]["md5"] == md5(OLD)                 # the row still says "old": retried as a missing file
    assert "the new file was not placed after its old copy moved to" in caplog.text
    # The verified new file is the post's only copy in its folder now: kept, under its staged name.
    staged = library["old"].parent / ("artist-safe-20.png" + ms.STAGED_SUFFIX)
    assert [p.name for p in library["old"].parent.iterdir()] == [staged.name] and staged.read_bytes() == NEW


def test_a_rename_that_succeeds_on_a_later_attempt_places_the_file(db, library, monkeypatch):
    rename, failures = os.rename, []

    def flaky(src, dst):
        if str(src).endswith(ms.STAGED_SUFFIX) and len(failures) < 2:
            failures.append(src)
            raise PermissionError("in use by a scanner")
        return rename(src, dst)

    monkeypatch.setattr(ms.os, "rename", flaky)
    assert ms.replace_changed_files(db, [library["post"]], ON)["replaced"] == 1
    assert library["new"].read_bytes() == NEW and len(failures) == 2


def test_a_staged_file_left_by_a_killed_run_is_replaced(db, library):
    stale = library["old"].parent / ("artist-safe-20.png" + ms.STAGED_SUFFIX)
    stale.write_bytes(b"half a file")
    assert ms.replace_changed_files(db, [library["post"]], ON)["replaced"] == 1
    assert library["new"].read_bytes() == NEW and not stale.exists()


def test_protected_and_undownloadable_files_are_kept(db, library, monkeypatch):
    other = library["root"] / "Commissioned" / "artist-safe-20.jpg"
    other.parent.mkdir()
    other.write_bytes(OLD)
    library["state"]["copies"].append(other)
    monkeypatch.setattr(ms.protected, "is_protected", lambda path: "Commissioned" in str(path))
    assert ms.replace_changed_files(db, [library["post"]], ON)["skipped"] == 1    # any copy, not just the first
    monkeypatch.setattr(ms.protected, "is_protected", lambda path: False)
    library["post"]["files"]["original"]["url"] = None      # e621 hides the file
    assert ms.replace_changed_files(db, [library["post"]], ON)["skipped"] == 1
    assert unchanged(db, library) and other.exists() and library["state"]["downloads"] == []


def test_a_file_two_posts_record_is_left_alone(db, library):
    db.add_post(21, file_path=str(library["old"]))
    assert ms.replace_changed_files(db, [library["post"]], ON)["skipped"] == 1
    assert unchanged(db, library) and library["state"]["downloads"] == []


def test_an_unrelated_file_at_the_new_name_is_never_overwritten(db, library):
    library["new"].write_bytes(b"someone else")
    assert ms.replace_changed_files(db, [library["post"]], ON)["skipped"] == 1
    assert library["new"].read_bytes() == b"someone else" and library["old"].read_bytes() == OLD


def test_replacing_is_off_until_enabled(db, library, caplog):
    with caplog.at_level(logging.INFO):
        stats = ms.replace_changed_files(db, [library["post"]], {})
    assert stats["skipped"] == 1 and library["state"]["downloads"] == [] and unchanged(db, library)
    assert "redownload_replaced_files: true" in caplog.text


def test_an_implausible_number_of_changed_files_replaces_nothing(db, library):
    over_the_floor = [library["post"]] * (ms.MAX_REPLACED + 1)
    stats = ms.replace_changed_files(db, over_the_floor, ON, compared=1000)
    assert stats["skipped"] == len(over_the_floor) and library["state"]["downloads"] == []
    # A large library may legitimately have more than the floor: the limit is 2% of the posts compared.
    compared = 50 * (ms.MAX_REPLACED + 1)
    assert ms.replace_changed_files(db, over_the_floor + [library["post"]], ON, compared=compared)["skipped"] == \
        ms.MAX_REPLACED + 2
    assert ms.replace_changed_files(db, over_the_floor, ON, compared=compared)["skipped"] == 0


def test_replacing_stops_after_failures_in_a_row(db, library, monkeypatch):
    def boom(url, target, headers):
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(ms, "_download", boom)
    stats = ms.replace_changed_files(db, [library["post"]] * 8, ON)
    assert stats["failed"] == ms.MAX_FAILURES_IN_A_ROW and stats["skipped"] == 8 - ms.MAX_FAILURES_IN_A_ROW


def test_an_unreachable_holding_folder_replaces_nothing(db, library, monkeypatch):
    blocker = library["root"] / "not-a-folder"
    blocker.write_bytes(b"")
    monkeypatch.setattr(ms.archive_paths, "replaced_directory", lambda: str(blocker / "Replaced"))
    assert ms.replace_changed_files(db, [library["post"]], ON)["skipped"] == 1
    assert unchanged(db, library) and library["state"]["downloads"] == []


def test_move_post_files_can_keep_a_copy(tmp_path, monkeypatch):
    class Ops:
        def log_move_operation(self, **kw):
            pass

    old, current = tmp_path / "a" / "x-safe-1.jpg", tmp_path / "b" / "x-safe-1.png"
    for path in (old, current):
        path.parent.mkdir()
        path.write_bytes(b"x")
    monkeypatch.setattr(post_files, "_operation_logger", lambda: Ops())
    monkeypatch.setattr(post_files, "find_post_files", lambda conn, ids: {1: [(str(old), None), (str(current), None)]})
    result = post_files.move_post_files(None, [1], tmp_path / "out", reason="x", keep=[str(current).upper()])
    assert result == {"moved": 1, "failed": 0, "cleared": {1}}
    assert current.exists() and (tmp_path / "out" / old.name).exists()


def test_a_move_that_copies_but_cannot_delete_the_source_leaves_no_copy_behind(tmp_path, monkeypatch):
    """Across drives a move is copy-then-delete; a file held open copies and then refuses the delete."""
    source = tmp_path / "lib" / "x-safe-2.jpg"
    source.parent.mkdir()
    source.write_bytes(b"x")

    def copy_then_fail(src, dst, **kw):
        with open(dst, "wb") as f:
            f.write(b"x")
        raise PermissionError("in use")

    monkeypatch.setattr(post_files.fileops, "move_file_with_logging", copy_then_fail)
    monkeypatch.setattr(post_files, "find_post_files", lambda conn, ids: {2: [(str(source), None)]})
    for _ in range(3):
        result = post_files.move_post_files(None, [2], tmp_path / "out", reason="x")
        assert result["failed"] == 1 and result["cleared"] == set()
    assert source.exists() and not list((tmp_path / "out").iterdir())


def test_a_downloaded_file_records_its_own_md5(tmp_path, monkeypatch):
    """The ingest keeps a stored md5 while a path is recorded; the download sets it for the file it fetched."""
    from fff.archive import download

    statements = []

    class Cursor:
        def execute(self, sql, params=()):
            statements.append((" ".join(sql.split()), tuple(params)))

        def fetchall(self):
            return [(7, "https://static/new7.png", "artist-safe-7.png", "safe")]

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

    class Response:
        status_code = 200

        def iter_content(self, chunk_size):
            return [NEW]

    monkeypatch.setattr(download, "api_get", lambda url, **kw: Response())
    monkeypatch.setattr(download, "generate_media_hash", lambda path, mysql_config=None: None)
    monkeypatch.setattr(download.time, "sleep", lambda seconds: None)
    download.download_missing_files(Conn(), {"missing_files_directory": str(tmp_path / "inbox")}, {})
    assert (tmp_path / "inbox" / "artist-safe-7.png").read_bytes() == NEW
    assert ("UPDATE posts SET md5 = %s WHERE id = %s", (md5(NEW), 7)) in statements


# --- the artist/rating rename ----------------------------------------------------------------

def test_a_rename_reaches_the_sorter_row_and_the_move_log(tmp_path, monkeypatch):
    new = tmp_path / "lib" / "new_artist-explicit-30.png"
    new.parent.mkdir()
    new.write_bytes(b"12345")
    old = str(tmp_path / "lib" / "old_artist-explicit-30.png")
    stored = old.replace("\\", "/")
    statements, logged = [], {}

    class Cursor:
        def execute(self, sql, params=()):
            statements.append((sql, list(params)))

        def fetchall(self):
            return [("nsfw_image", stored), ("bad table; drop", stored)]

        def close(self):
            pass

    class Conn:
        commits = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            Conn.commits += 1

    class Ops:
        def log_rename_operation(self, old_name, new_name, directory, **kw):
            logged.update(old=old_name, new=new_name, **kw)

    monkeypatch.setattr(post_files, "_operation_logger", lambda: Ops())
    monkeypatch.setattr(post_files.archive_paths, "db_names", lambda: {"sorter": "image_sorting_hashes"})
    assert post_files.record_rename(Conn(), old, str(new), reason="artist_rename") == 1
    assert logged == {"old": "old_artist-explicit-30.png", "new": new.name, "file_size": 5, "reason": "artist_rename"}
    select, update = statements
    assert "`image_sorting_hashes`.all_files" in select[0] and stored in select[1]
    # The artist scan's cached verdict belonged to the old name.
    assert update[0] == ("UPDATE `image_sorting_hashes`.`nsfw_image` SET file_name = %s, file_path = %s, "
                         "last_artist_check = NULL, artist_matched = NULL WHERE file_path = %s")
    assert update[1] == [new.name, str(new).replace("\\", "/"), stored]
    assert Conn.commits == 1


def test_a_failed_sorter_update_never_fails_the_rename(tmp_path, monkeypatch):
    class Conn:
        def cursor(self):
            raise RuntimeError("server gone")

        def rollback(self):
            pass

    monkeypatch.setattr(post_files, "_operation_logger", lambda: (_ for _ in ()).throw(RuntimeError("no logger")))
    assert post_files.record_rename(Conn(), str(tmp_path / "a.png"), str(tmp_path / "b.png"), reason="x") == 0


# --- the real statements, against the test_* databases ---------------------------------------

IDS = (2_000_000_901, 2_000_000_902)      # far above any real post, pool or tag


@pytest.mark.integration
def test_round_trip_in_mysql(tmp_path, monkeypatch):
    from fff.archive import paths as archive_paths
    from fff.archive.config import load_config
    from fff.archive.db import connect_to_mysql

    names = archive_paths.db_names()
    assert names["archive"].startswith("test_") and names["sorter"].startswith("test_")
    sorter = names["sorter"]
    conn = connect_to_mysql(load_config()["mysql"])
    cursor = conn.cursor()
    a, b = IDS
    marks = "%s, %s"
    library = tmp_path / "lib"
    library.mkdir()
    renamed_from = (library / f"old-safe-{a}.png").as_posix()
    renamed_to = library / f"new-safe-{a}.png"
    old_file = library / f"artist-explicit-{b}.jpg"
    test_names = [f"old-safe-{a}.png", f"new-safe-{a}.png", f"artist-explicit-{b}.jpg", f"artist-explicit-{b}.png"]
    name_marks = ", ".join(["%s"] * len(test_names))

    def cleanup():
        cursor.execute(f"DELETE FROM posts WHERE id IN ({marks})", IDS)       # links cascade
        cursor.execute(f"DELETE FROM pools WHERE pool_id IN ({marks})", IDS)
        cursor.execute("DELETE FROM tags WHERE name = 'fff_test_recategorised'")
        cursor.execute(f"DELETE FROM `{sorter}`.other_directories WHERE file_name IN ({name_marks})", test_names)
        cursor.execute(f"DELETE FROM `{sorter}`.file_operations WHERE file_name IN ({name_marks})", test_names)
        conn.commit()

    def column(sql, params=IDS):
        cursor.execute(sql, params)
        return cursor.fetchall()

    try:
        cleanup()
        cursor.executemany(
            "INSERT INTO posts (id, file_url, md5, rating, deleted, deleted_at, deletion_reason, updated_at, "
            "file_path, file_name, media_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(a, "https://e621.net/posts/1", "a" * 32, "safe", 1, "2026-01-01 00:00:00", "gone", "2026-01-01 00:00:00",
              None, None, None),
             (b, "https://static/b.jpg", md5(OLD), "explicit", 0, None, None, "2026-01-01 00:00:00",
              str(old_file), old_file.name, "old-hash")])
        cursor.execute("INSERT INTO pools (pool_id) VALUES (%s)", (a,))
        cursor.execute("INSERT INTO posts_pools (post_id, pool_id) VALUES (%s, %s)", (a, a))
        cursor.executemany("INSERT INTO source_links (post_id, source_link) VALUES (%s, %s)",
                           [(a, "https://e621.net/posts/1"), (a, "https://Kept.example/x"), (b, "https://b.example")])
        cursor.execute("INSERT INTO post_relationships (post_id, parent_id, child_id) VALUES (%s, NULL, %s)", (a, 5))
        cursor.execute("INSERT INTO tags (name, tag_type) VALUES ('fff_test_recategorised', 'General')")
        conn.commit()

        post_a = api_post(a, rating="q", updated_at="2026-11-19T16:46:04.291Z", pools=[b],
                          sources=["https://kept.example/x", r"https://new.example/\with\backslashes"],
                          relationships={"parent_id": b, "children": [6, 7]},
                          tags={"species": ["fff_test_recategorised"], "artist": ["someone"]})
        post_a["files"] = {"meta": {"md5": "a" * 32, "ext": "png"}, "original": {"url": "https://static/a.png"}}
        post_b = api_post(b, rating="e", sources=["https://b.example"], updated_at="2026-01-01T00:00:00")
        post_b["files"] = {"meta": {"md5": md5(NEW), "ext": "png"}, "original": {"url": "https://static/b2.png"}}
        posts = [post_a, post_b]

        # --- everything but the file ---
        stats = ms.sync_changed_post_metadata(conn, posts)
        assert stats["compared"] == 2 and stats["file_changed"] == [post_b] and stats["new_pool_ids"] == {b}
        assert (stats["rating"], stats["restored"], stats["file_url"], stats["sources"], stats["pools"],
                stats["relationships"]) == (1, 1, 1, 1, 1, 1)
        assert column(f"SELECT id, rating, deleted, deleted_at, deletion_reason, md5, file_url, updated_at "
                      f"FROM posts WHERE id IN ({marks}) ORDER BY id") == [
            (a, "questionable", 0, None, None, "a" * 32, "https://static/a.png", local("2026-11-19T16:46:04.291Z")),
            (b, "explicit", 0, None, None, md5(OLD), "https://static/b.jpg", local("2026-01-01T00:00:00"))]
        # Sources are only added: the first link, which e621 no longer lists, stays.
        assert sorted(column(f"SELECT post_id, source_link FROM source_links WHERE post_id IN ({marks})")) == [
            (a, "https://Kept.example/x"), (a, "https://e621.net/posts/1"),
            (a, r"https://new.example/\with\backslashes"), (b, "https://b.example")]
        assert column(f"SELECT post_id, pool_id FROM posts_pools WHERE post_id IN ({marks})") == [(a, b)]
        assert sorted(column(f"SELECT parent_id, child_id FROM post_relationships WHERE post_id IN ({marks})"),
                      key=str) == sorted([(b, None), (None, 6), (None, 7)], key=str)

        # The same check for posts the full ingest rewrote: flag already cleared, the rest left behind.
        cursor.execute("UPDATE posts SET deleted_at = '2026-01-01 00:00:00', deletion_reason = 'gone' WHERE id = %s", (a,))
        conn.commit()
        assert ms.clear_restored_posts(conn, posts) == [a]
        assert column("SELECT deleted, deleted_at, deletion_reason FROM posts WHERE id = %s", (a,)) == [(0, None, None)]
        assert ms.clear_restored_posts(conn, posts) == []

        again = ms.sync_changed_post_metadata(conn, posts)      # settled: nothing left to write
        assert not any(again[k] for k in CHANGE_COUNTS)

        assert ms.sync_tag_types(conn, posts) >= 1
        assert column("SELECT tag_type FROM tags WHERE name = 'fff_test_recategorised'", ()) == [("Species",)]

        assert ms.stamp_last_checked(conn, posts, datetime(2026, 11, 19, 13, 27, 4)) == 2
        assert column(f"SELECT DISTINCT lastChecked FROM posts WHERE id IN ({marks})") == [(datetime(2026, 11, 19, 13, 27, 4),)]

        # --- the file: the real lookups, moves, journal rows and post-row statements ---
        old_file.write_bytes(OLD)
        cursor.execute(f"INSERT INTO `{sorter}`.other_directories (file_name, file_path, e621_post_id, md5_hash) "
                       "VALUES (%s, %s, %s, %s)", (old_file.name, old_file.as_posix(), b, md5(OLD)))
        conn.commit()
        monkeypatch.setattr(ms, "_download", lambda url, target, headers: target.write_bytes(NEW))
        monkeypatch.setattr(ms.archive_paths, "replaced_directory", lambda: str(tmp_path / "held"))
        monkeypatch.setattr(ms.archive_paths, "TEMP_DOWNLOADS", tmp_path / "temp", raising=False)
        outcome = ms.replace_changed_files(conn, [post_b], ON)
        assert outcome == {"candidates": 1, "replaced": 1, "md5_refreshed": 0, "skipped": 0, "failed": 0}
        new_file = old_file.with_suffix(".png")
        assert new_file.read_bytes() == NEW and (tmp_path / "held" / old_file.name).read_bytes() == OLD
        assert column("SELECT file_path, file_name, md5, file_url, media_hash, file_type FROM posts WHERE id = %s", (b,)) == [
            (ms.normalize_file_path(str(new_file)), new_file.name, md5(NEW), "https://static/b2.png", None, "image")]
        assert column(f"SELECT COUNT(*) FROM `{sorter}`.other_directories WHERE file_name = %s", (old_file.name,)) == [(0,)]
        assert sorted(column(f"SELECT file_name, reason FROM `{sorter}`.file_operations WHERE file_name IN ({name_marks})",
                             test_names)) == [(old_file.name, "archive:replaced_on_e621"),
                                              (new_file.name, "archive:replaced_on_e621:new_file")]
        # Settled: the post is no longer a candidate, and a local file that already matches only fixes the row.
        assert ms.sync_changed_post_metadata(conn, posts)["file_changed"] == []
        cursor.execute("UPDATE posts SET md5 = %s WHERE id = %s", (md5(OLD), b))
        conn.commit()
        assert ms.replace_changed_files(conn, [post_b], ON)["md5_refreshed"] == 1
        assert column("SELECT md5, file_path FROM posts WHERE id = %s", (b,)) == [
            (md5(NEW), ms.normalize_file_path(str(new_file)))]

        # --- a full re-ingest keeps the stored md5 of a post that has a file ---
        post_b["files"]["meta"]["md5"] = "f" * 32
        post_b["local_file_path"] = None
        ms.insert_or_update_posts_bulk(conn, [post_b])
        assert column("SELECT md5 FROM posts WHERE id = %s", (b,)) == [(md5(NEW),)]
        assert ms.sync_changed_post_metadata(conn, [post_b])["file_changed"] == [post_b]   # so it is still noticed

        # --- a rename reaches the sorter row, whichever way the old path is spelled ---
        cursor.execute(f"INSERT INTO `{sorter}`.other_directories (file_name, file_path, e621_post_id, "
                       "last_artist_check, artist_matched) VALUES (%s, %s, %s, NOW(), 'someone')",
                       (f"old-safe-{a}.png", renamed_from, a))
        conn.commit()
        renamed_to.write_bytes(b"x")
        assert post_files.record_rename(conn, renamed_from.replace("/", "\\"), str(renamed_to), reason="test") == 1
        assert column(f"SELECT file_name, file_path, e621_post_id, last_artist_check, artist_matched "
                      f"FROM `{sorter}`.other_directories WHERE e621_post_id = %s", (a,)) == [
            (renamed_to.name, renamed_to.as_posix(), a, None, None)]
        assert column(f"SELECT operation_type, reason FROM `{sorter}`.file_operations WHERE file_name = %s",
                      (renamed_to.name,)) == [("rename", "archive:test")]
    finally:
        conn.rollback()
        cleanup()
        cursor.close()
        conn.close()
