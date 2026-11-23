"""The nightly artist/rating rename fixes post files that carry the wrong post ID, when the
file's md5 proves whose file it is.

Six files were named with a parent's (or sibling's) ID while holding the child's image, and
both posts pointed at them. The rename skipped them every night as "Post ID mismatch",
trusting the name over the contents. Files named "-1" or with a double extension
(".mp4.webm") were skipped because the ID couldn't be read.
"""
import hashlib

import pytest

from fff.archive import db, post_files


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class Posts:
    """An in-memory posts table behind the connection calls the rename step makes."""

    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.commits = self.rollbacks = 0

    def cursor(self, dictionary=False):
        table = self

        class Cursor:
            result = []

            def execute(self, sql, params=()):
                sql = " ".join(sql.split())
                if sql.startswith("SELECT p.id, p.file_name, p.file_path, p.rating, p.file_type, p.md5"):
                    self.result = [dict(r) for r in sorted(table.rows.values(), key=lambda r: r["id"])
                                   if r["file_path"] and not r.get("deleted")]
                elif sql == "SELECT id FROM posts WHERE file_path = %s AND id <> %s":
                    self.result = [(i,) for i, r in table.rows.items() if r["file_path"] == params[0] and i != params[1]]
                elif sql.startswith("UPDATE posts SET file_path = NULL, file_name = NULL WHERE id IN"):
                    for i in params:
                        table.rows[i]["file_path"] = table.rows[i]["file_name"] = None
                elif sql == "UPDATE posts SET file_path = %s, file_name = %s WHERE id = %s":
                    table.rows[params[2]].update(file_path=params[0], file_name=params[1])
                elif sql == "UPDATE posts SET file_name = %s WHERE id = %s":
                    table.rows[params[1]]["file_name"] = params[0]
                else:
                    raise AssertionError(sql)

            def fetchall(self):
                return self.result

            def close(self):
                pass

        return Cursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def archive(monkeypatch, tmp_path):
    artists = {}
    renames = []
    monkeypatch.setattr(db, "get_post_artists", lambda conn, pid: artists.get(pid, ["unknown"]))
    monkeypatch.setattr(db, "log_artist_file_rename", lambda *a, **kw: None)
    monkeypatch.setattr(post_files, "record_rename",
                        lambda conn, old, new, reason: renames.append((old, new, reason)))
    folder = tmp_path / "Tag Sorted"
    folder.mkdir()
    return folder, artists, renames


def post(pid, path, data_md5, rating="e"):
    return {"id": pid, "file_name": None, "file_path": str(path), "rating": rating, "file_type": "image",
            "md5": data_md5}


def test_a_child_file_under_its_parents_id_is_renamed_and_the_parent_unlinked(archive):
    folder, artists, renames = archive
    artists.update({6340561: ["anchee"], 6768337: ["anchee"]})
    misnamed = folder / "anchee-explicit-6340561.png"
    misnamed.write_bytes(b"child image")
    table = Posts([post(6340561, misnamed, md5(b"parent image")),      # the parent's real file is gone
                   post(6768337, misnamed, md5(b"child image"))])

    stats = db.check_and_update_file_artist_names(table)

    fixed = folder / "anchee-explicit-6768337.png"
    assert fixed.read_bytes() == b"child image" and not misnamed.exists()
    assert table.rows[6768337]["file_path"].endswith("anchee-explicit-6768337.png")
    assert table.rows[6340561]["file_path"] is None          # queued for the missing-file download
    assert stats["misnamed_reclaimed"] == 1 and stats["post_id_mismatch_skipped"] == 0
    assert renames == [(str(misnamed), str(fixed), "misnamed_post_file")]


def test_a_mismatched_file_whose_contents_are_not_this_posts_is_still_skipped(archive):
    folder, artists, renames = archive
    other = folder / "bloo-explicit-3716476.png"
    other.write_bytes(b"post 3716476's image")
    table = Posts([post(3716478, other, md5(b"post 3716478's image"))])

    stats = db.check_and_update_file_artist_names(table)

    assert other.exists() and table.rows[3716478]["file_path"] == str(other)
    assert stats["post_id_mismatch_skipped"] == 1 and stats["misnamed_reclaimed"] == 0 and not renames


@pytest.mark.parametrize("name, fixed", [
    ("jishinu-explicit-5789536-1.png", "jishinu-explicit-5789536.png"),
    ("jishinu-explicit-5789536.mp4.webm", "jishinu-explicit-5789536.webm"),
    ("jishinu-explicit-5789536.jpg.webp", "jishinu-explicit-5789536.webp"),
])
def test_copies_and_double_extensions_of_the_posts_own_file_are_renamed(archive, name, fixed):
    folder, artists, _ = archive
    artists[5789536] = ["jishinu"]
    (folder / name).write_bytes(b"image")
    table = Posts([post(5789536, folder / name, md5(b"image"))])

    db.check_and_update_file_artist_names(table)

    assert (folder / fixed).exists() and not (folder / name).exists()
    assert table.rows[5789536]["file_path"].endswith(fixed)


@pytest.mark.parametrize("name", ["Zuruzuking - SmergZuruPads.png",
                                  "Mari-bluesky_did_plc_mbmiokeltsjrirjhevik47u2-3n2mk7mytm32y-1.jpg"])
def test_files_named_by_another_site_or_the_artist_are_left_alone(archive, name):
    folder, artists, renames = archive
    (folder / name).write_bytes(b"image")
    table = Posts([post(6163718, folder / name, md5(b"image"))])

    stats = db.check_and_update_file_artist_names(table)

    assert (folder / name).exists() and not renames
    assert stats["misnamed_reclaimed"] == 0 and stats["skipped_no_change"] == 1


def test_dry_run_changes_nothing(archive):
    folder, artists, renames = archive
    misnamed = folder / "anchee-explicit-6340561.png"
    misnamed.write_bytes(b"child image")
    table = Posts([post(6340561, misnamed, md5(b"parent image")), post(6768337, misnamed, md5(b"child image"))])

    stats = db.check_and_update_file_artist_names(table, dry_run=True)

    assert misnamed.exists() and table.rows[6340561]["file_path"] == str(misnamed) and not renames
    assert stats["misnamed_reclaimed"] == 1
