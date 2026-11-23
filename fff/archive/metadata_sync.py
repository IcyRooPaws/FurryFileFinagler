"""Keep posts whose file is already on disk in step with e621.

``fff e621 sync`` re-ingests a post in full only when it is new, deleted on e621, or its file is
missing. Every other favorite used to get its tag links compared and nothing else, so its rating,
sources, pools, relationships, deleted flag and the file itself stayed as they were on the day
the post was first ingested. e621 is the source of truth for all of these. This module compares
each fetched post with the database and rewrites only what differs:

- ``sync_changed_post_metadata``: the posts row (rating, restored posts, file URL, updated_at),
  source links (added only, see below), pool membership and parent/child relationships.
- ``clear_restored_posts``: the same "no longer deleted" check for the posts the ingest did
  rewrite, which keeps an old deletion date and reason.
- ``replace_changed_files``: posts whose file e621 has replaced since they were ingested.
- ``sync_tag_types``: the category of every tag (e621 moves tags between categories).
- ``stamp_last_checked``: posts.lastChecked, so it says when a post was last compared with e621.

Source links are the exception to "e621 is the source of truth": they are only ever added. A
link e621 later removes from a post is kept here, as a record of where the post was once found.

Pools and relationships are pruned only when the response actually carries them (a ``pools``
list, a ``relationships`` object), the same rule ``_has_tag_list`` applies to tags: a response
without the field must never read as "e621 has none".
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from fff.archive import paths as archive_paths
from fff.archive import post_files
from fff.archive.config import normalize_file_path
from fff.archive.db import (_get_file_ext, _get_file_md5, _get_file_url, _has_tag_list, get_tag_type,
                            insert_or_update_posts_bulk, resolve_existing_file_path)
from fff.clients.e621 import api_get
from fff.common import fileops, protected
from fff.common.naming import windows_safe_filename

RATINGS = {"s": "safe", "q": "questionable", "e": "explicit"}
_CHUNK = 500
# More changed files than this in one run (or 2% of the posts compared, if that is more) is
# treated as a broken response, not as e621 replacing that many files.
MAX_REPLACED = 500
# Stop replacing after this many failures in a row (the file host or a drive is down).
MAX_FAILURES_IN_A_ROW = 5
# More posts than this (or 1% of the posts compared) losing every pool or relationship link in
# one run is treated as a broken response too.
MAX_EMPTIED = 100
# The new file waits under this name, in its final folder, while the old copies move out. The
# sorter ignores the extension and no file-name parser reads a post id from it.
STAGED_SUFFIX = ".fff-new"
RENAME_ATTEMPTS = 5


def _rating(post) -> str:
    rating = str(post.get("rating") or "unknown").lower()
    return RATINGS.get(rating, rating)


def _local_time(value):
    """An e621 timestamp as posts stores it: local time, whole seconds, no timezone."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None


def _source_key(link: str) -> str:
    """What makes two source links the same row: source_links' key is the first 255 characters,
    compared without case."""
    return link[:255].casefold()


def _wanted_sources(post):
    """``{key: link}`` e621 lists for this post (empty or None when it lists none)."""
    sources = post.get("sources")
    if not isinstance(sources, list):
        return None
    links = [s[:1000] for s in sources if isinstance(s, str) and s]
    if (post.get("flags") or {}).get("deleted"):
        links.append(f"https://e621.net/posts/{post['id']}")   # as insert_post_sources adds
    wanted = {}
    for link in links:
        wanted.setdefault(_source_key(link), link)
    return wanted


def _wanted_relationships(post):
    """``{(parent_id, child_id)}`` rows as post_relationships holds them, or None when absent."""
    relationships = post.get("relationships")
    if (not isinstance(relationships, dict) or "parent_id" not in relationships
            or not isinstance(relationships.get("children"), list)):
        return None      # half an object is not "no parent" or "no children"
    rows = set()
    if relationships.get("parent_id"):
        rows.add((relationships["parent_id"], None))
    rows.update((None, child) for child in relationships.get("children") or [])
    return rows


def _grouped(cursor, sql, params) -> dict:
    """``{first column: {rest of the row, or the single other column}}``."""
    cursor.execute(sql, params)
    out: dict = {}
    for row in cursor.fetchall():
        out.setdefault(row[0], set()).add(row[1] if len(row) == 2 else tuple(row[1:]))
    return out


def _many(cursor, sql, rows) -> None:
    if rows:
        cursor.executemany(sql, rows)


def sync_changed_post_metadata(conn, posts_data, batch_size: int = _CHUNK) -> dict:
    """Bring everything but the tag links and the file of these posts in line with e621.

    For posts the sync does not re-ingest (file already on disk). Every post is compared first;
    the differences are then written in one transaction, so an error leaves nothing half done.
    Returns counts of posts changed per field, plus ``new_pool_ids`` (pools that gained a link,
    for the metadata fetch) and ``file_changed`` (posts whose e621 md5 differs from the stored
    one, for ``replace_changed_files``).
    """
    stats = {"compared": 0, "rating": 0, "restored": 0, "restored_ids": [], "md5": 0, "file_url": 0,
             "updated_at": 0, "sources": 0, "pools": 0, "relationships": 0, "new_pool_ids": set(),
             "file_changed": []}
    plan = {"ratings": [], "restored": [], "md5s": [], "urls": [], "stamps": [],
            "sources": [], "pools": [], "relationships": []}
    # A favorite added while the pages were being fetched lists one post twice. Planned twice,
    # its relationship rows would be inserted twice (their unique key holds NULLs).
    posts = list({p["id"]: p for p in posts_data if p.get("id")}.values())
    for start in range(0, len(posts), batch_size):
        _compare_batch(conn, posts[start:start + batch_size], stats, plan)
    _drop_implausible_prunes(plan, stats["compared"])
    _write(conn, plan, stats)

    changed = {k: stats[k] for k in ("rating", "restored", "md5", "file_url", "updated_at", "sources", "pools",
                                     "relationships") if stats[k]}
    if changed:
        logging.info("Updated already-downloaded posts that changed on e621: %s",
                     ", ".join(f"{name} {count}" for name, count in changed.items()))
    else:
        logging.info("No metadata changes for already-downloaded posts")
    _log_restored(stats["restored_ids"])
    if stats["file_changed"]:
        logging.info("%d already-downloaded post(s) have a different file on e621 now", len(stats["file_changed"]))
    return stats


# A restored post starts clean: a later deletion then records its own date and reason.
_RESTORE = "UPDATE posts SET deleted = 0, deleted_at = NULL, deletion_reason = NULL WHERE id = %s"
_HAS_DELETION_STATE = "(deleted = 1 OR deleted_at IS NOT NULL OR (deletion_reason IS NOT NULL AND deletion_reason <> ''))"
# What a deleted post's file_url holds, since e621 gives no file URL for it.
_POST_PAGE = "https://e621.net/posts/"


def _log_restored(post_ids) -> None:
    if post_ids:
        logging.info("No longer deleted on e621 (deleted flag, date and reason cleared): %s",
                     ", ".join(str(post_id) for post_id in sorted(post_ids)))


def clear_restored_posts(conn, posts_data) -> list:
    """Finish un-deleting the posts the full ingest just rewrote. Returns the post ids cleared.

    The ingest sets ``deleted`` from e621 but keeps an existing deletion date and reason, so a
    restored post that went through it (its file was missing, say) kept both, and nothing in
    the log said it had come back. ``sync_changed_post_metadata`` does the same for the posts
    the ingest skips. Only posts whose response carries a flags object count as "not deleted".
    """
    ids = [p["id"] for p in posts_data
           if p.get("id") and isinstance(p.get("flags"), dict) and not p["flags"].get("deleted")]
    restored = []
    cursor = conn.cursor()
    try:
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            cursor.execute(f"SELECT id FROM posts WHERE id IN ({','.join(['%s'] * len(chunk))}) "
                           f"AND {_HAS_DELETION_STATE}", chunk)
            restored += [row[0] for row in cursor.fetchall()]
        _many(cursor, _RESTORE, [(post_id,) for post_id in restored])
        conn.commit()
    finally:
        cursor.close()
    _log_restored(restored)
    return sorted(restored)


def _compare_batch(conn, batch, stats, plan) -> None:
    """Add what differs between these posts and the database to ``plan``. Reads only."""
    ids = [post["id"] for post in batch]
    marks = ",".join(["%s"] * len(ids))
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT id, rating, md5, file_url, updated_at, {_HAS_DELETION_STATE} "
            f"FROM posts WHERE id IN ({marks})", ids)
        stored = {row[0]: row[1:] for row in cursor.fetchall()}
        links = _grouped(cursor, f"SELECT post_id, source_link FROM source_links WHERE post_id IN ({marks})", ids)
        pools = _grouped(cursor, f"SELECT post_id, pool_id FROM posts_pools WHERE post_id IN ({marks})", ids)
        relations = _grouped(
            cursor, f"SELECT post_id, parent_id, child_id FROM post_relationships WHERE post_id IN ({marks})", ids)
    finally:
        cursor.close()

    for post in batch:
        post_id = post["id"]
        if post_id not in stored:    # not in the database yet: the full ingest's job
            continue
        stats["compared"] += 1
        rating, md5, file_url, updated_at, marked_deleted = stored[post_id]

        if post.get("rating") and _rating(post) != rating:
            plan["ratings"].append((_rating(post), post_id))
        flags = post.get("flags")
        # Also a post the full ingest already un-deleted: that keeps the old date and reason.
        if marked_deleted and isinstance(flags, dict) and not flags.get("deleted"):
            plan["restored"].append((post_id,))

        api_md5, api_url = (_get_file_md5(post) or "").lower(), _get_file_url(post)
        if api_md5 and md5 and api_md5 != md5.lower():
            stats["file_changed"].append(post)     # md5 and URL move together, with the file
            # ...except a deleted post's placeholder URL: it must not outlive the deletion, even
            # when the file replacement is skipped.
            if api_url and (file_url or "").startswith(_POST_PAGE):
                plan["urls"].append((api_url, post_id))
        else:
            if api_md5 and not md5:
                plan["md5s"].append((api_md5, post_id))
            if api_url and api_url != file_url:
                plan["urls"].append((api_url, post_id))

        when = _local_time(post.get("updated_at"))
        if when and when != updated_at:
            plan["stamps"].append((when, post_id))

        # Sources are only added: a link e621 has since removed stays (see the module docstring).
        wanted = _wanted_sources(post)
        if wanted:
            have = {_source_key(link) for link in links.get(post_id, ())}
            add = [link for key, link in wanted.items() if key not in have]
            if add:
                plan["sources"].append({"id": post_id, "add": add})

        api_pools = post.get("pools")
        if isinstance(api_pools, list):
            want, have_pools = set(api_pools), pools.get(post_id, set())
            if want != have_pools:
                plan["pools"].append({"id": post_id, "drop": sorted(have_pools - want),
                                      "add": sorted(want - have_pools), "emptied": not want})

        rows = _wanted_relationships(post)
        if rows is not None and rows != relations.get(post_id, set()):
            plan["relationships"].append({"id": post_id, "rows": rows, "emptied": not rows})


def _drop_implausible_prunes(plan, compared) -> None:
    """Refuse to empty the links of an implausible number of posts.

    A response that carries the field but has it empty on every post (a changed API mode, say)
    would otherwise delete every pool or relationship link in one run. Posts that lose only
    some links, or gain any, are unaffected.
    """
    limit = max(MAX_EMPTIED, compared // 100)
    for kind in ("pools", "relationships"):
        emptied = [change for change in plan[kind] if change["emptied"]]
        if len(emptied) > limit:
            logging.error("e621 lists no %s for %d posts that have some here, more than the %d a run will clear. "
                          "Their %s were left alone; check the favorites response.", kind, len(emptied), limit, kind)
            plan[kind] = [change for change in plan[kind] if not change["emptied"]]


def _write(conn, plan, stats) -> None:
    add_links = [(c["id"], link) for c in plan["sources"] for link in c["add"]]
    drop_pools = [(c["id"], pool) for c in plan["pools"] for pool in c["drop"]]
    add_pools = [(c["id"], pool) for c in plan["pools"] for pool in c["add"]]
    new_pools = sorted({pool for _, pool in add_pools})
    relinked = [c["id"] for c in plan["relationships"]]
    relation_rows = [(c["id"], *row) for c in plan["relationships"] for row in sorted(c["rows"], key=str)]

    cursor = conn.cursor()
    try:
        _many(cursor, "UPDATE posts SET rating = %s WHERE id = %s", plan["ratings"])
        _many(cursor, _RESTORE, plan["restored"])
        _many(cursor, "UPDATE posts SET md5 = %s WHERE id = %s", plan["md5s"])
        _many(cursor, "UPDATE posts SET file_url = %s WHERE id = %s", plan["urls"])
        _many(cursor, "UPDATE posts SET updated_at = %s WHERE id = %s", plan["stamps"])
        _many(cursor, "INSERT IGNORE INTO source_links (post_id, source_link) VALUES (%s, %s)", add_links)
        _many(cursor, "INSERT IGNORE INTO pools (pool_id) VALUES (%s)", [(pool,) for pool in new_pools])
        _many(cursor, "INSERT IGNORE INTO posts_pools (post_id, pool_id) VALUES (%s, %s)", add_pools)
        _many(cursor, "DELETE FROM posts_pools WHERE post_id = %s AND pool_id = %s", drop_pools)
        # A post's relationship rows are replaced as a set: the table's unique key holds NULLs,
        # so it would not stop a re-inserted row from becoming a duplicate.
        for start in range(0, len(relinked), _CHUNK):
            chunk = relinked[start:start + _CHUNK]
            cursor.execute(
                f"DELETE FROM post_relationships WHERE post_id IN ({','.join(['%s'] * len(chunk))})", chunk)
        _many(cursor, "INSERT INTO post_relationships (post_id, parent_id, child_id) VALUES (%s, %s, %s)",
              relation_rows)
        conn.commit()
    finally:
        cursor.close()

    stats["rating"] = len(plan["ratings"])
    stats["restored"] = len(plan["restored"])
    stats["restored_ids"] = sorted(post_id for (post_id,) in plan["restored"])
    stats["md5"] = len(plan["md5s"])
    stats["file_url"] = len(plan["urls"])
    stats["updated_at"] = len(plan["stamps"])
    stats["sources"] = len(plan["sources"])
    stats["pools"] = len(plan["pools"])
    stats["relationships"] = len(plan["relationships"])
    stats["new_pool_ids"].update(new_pools)


def stamp_last_checked(conn, posts_data, when: datetime | None = None) -> int:
    """Set posts.lastChecked for every post that came with a real tag list (so was compared).

    The full ingest stamps the posts it rewrites; without this the posts it skips kept the date
    of their first ingest, however often they were compared since.
    """
    ids = [p["id"] for p in posts_data if p.get("id") and _has_tag_list(p)]
    when = (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.cursor()
    try:
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            cursor.execute(f"UPDATE posts SET lastChecked = %s WHERE id IN ({','.join(['%s'] * len(chunk))})",
                           [when, *chunk])
        conn.commit()
    finally:
        cursor.close()
    return len(ids)


def sync_tag_types(conn, posts_data) -> int:
    """Update tags.tag_type for tags e621 has moved to another category. Returns the number changed.

    The ingest writes a tag's type once, when the tag is first seen (INSERT IGNORE), so a later
    recategorisation never reached the database.
    """
    wanted = {}
    for post in posts_data:
        if not _has_tag_list(post):
            continue
        for category, names in post["tags"].items():
            if category == "artist":     # artists live in their own table
                continue
            tag_type = get_tag_type(category)
            for name in names:
                wanted[name] = tag_type
    if not wanted:
        return 0
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT tag_id, name, tag_type FROM tags")
        changed = [(wanted[name], tag_id) for tag_id, name, tag_type in cursor.fetchall()
                   if name in wanted and wanted[name] != tag_type]
        _many(cursor, "UPDATE tags SET tag_type = %s WHERE tag_id = %s", changed)
        conn.commit()
    finally:
        cursor.close()
    if changed:
        logging.info("Updated the category of %d tag(s) that e621 recategorised", len(changed))
    return len(changed)


# --- files e621 has replaced ---------------------------------------------------------------

_md5_of = fileops.md5_of_file


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _download(url, target, headers) -> None:
    response = api_get(url, headers=headers, stream=True)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} for {url}")
    with open(target, "wb") as f:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)


def _discard(path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _movable(path) -> bool:
    """False when another program holds the file open (Windows refuses to rename it then)."""
    try:
        os.rename(path, path)
        return True
    except OSError:
        return False


def _rename_into_place(staged, target) -> None:
    """Rename on the same volume, riding out a scanner or indexer that has the new file open."""
    for attempt in range(RENAME_ATTEMPTS):
        try:
            os.rename(staged, target)
            return
        except OSError:
            if attempt == RENAME_ATTEMPTS - 1:
                raise
            time.sleep(0.5)


def _with_e621_extension(path, stem, ext) -> str:
    """Give a file that has e621's content but an older extension e621's extension.

    e621 replacing a jpg with a png leaves files whose bytes are the png but whose name still
    says jpg. Returns the path the file is at afterwards; any obstacle leaves it as it is.
    """
    if not ext or os.path.splitext(path)[1].lower() == f".{ext}":
        return path
    renamed = os.path.join(os.path.dirname(path), windows_safe_filename(f"{stem}.{ext}"))
    if protected.is_protected(path) or os.path.exists(renamed):
        return path
    try:
        os.rename(path, renamed)
    except OSError as e:
        logging.warning("Could not rename %s to %s: %s", path, os.path.basename(renamed), e)
        return path
    return renamed


def _forget_perceptual_hash(cursor, post_id) -> None:
    """The cached perceptual hash of e621's file described the old file."""
    cursor.execute("DELETE FROM e621_perceptual_hashes WHERE post_id = %s", (post_id,))


def replace_changed_files(conn, posts, e621_config, compared: int = 0) -> dict:
    """Give each of these posts e621's current file. ``posts`` is ``file_changed`` from above.

    A post that already has e621's current file locally only gets its row corrected (and any
    other, older copies moved out). Otherwise, in this order:

    1. the new file is downloaded and checked against e621's md5;
    2. it is copied into the folder of the file posts.file_path names, under a temporary name;
    3. every local copy of the old file moves to ``archive.e621.replaced_directory`` (kept, not
       deleted);
    4. the new file is renamed into place, so a file sorted or placed by hand stays where it was.

    A failure in steps 1-3 leaves the old file where it is, to be retried on the next run. Only
    a failed rename in step 4 can leave the folder without the file; the old one is then in the
    replaced directory and the following runs download the new one as a missing file.

    Off unless ``archive.e621.redownload_replaced_files`` is true. ``compared`` (posts compared
    this run) scales the guard against a broken response. Returns counts: candidates, replaced,
    md5_refreshed, skipped, failed.
    """
    stats = {"candidates": len(posts), "replaced": 0, "md5_refreshed": 0, "skipped": 0, "failed": 0}
    if not posts:
        return stats
    if not e621_config.get("redownload_replaced_files", False):
        logging.info("%d post(s) have a different file on e621 than the one ingested; set "
                     "redownload_replaced_files: true to replace the local files", len(posts))
        stats["skipped"] = len(posts)
        return stats
    limit = max(MAX_REPLACED, compared // 50)
    if len(posts) > limit:
        logging.error("%d posts report a different file on e621, more than the %d a run will replace. "
                      "Nothing was replaced; check the favorites response before running again.",
                      len(posts), limit)
        stats["skipped"] = len(posts)
        return stats

    holding = archive_paths.replaced_directory()
    temp_dir = Path(archive_paths.TEMP_DOWNLOADS)
    try:
        Path(holding).mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.error("Cannot replace files that changed on e621: %s", e)
        stats["skipped"] = len(posts)
        return stats

    headers = {"User-Agent": e621_config.get("user_agent", "e621 archive/1.0")}
    replaced, failures_in_a_row = [], 0
    for index, post in enumerate(posts):
        try:
            outcome = _replace_one(conn, post, holding, temp_dir, headers)
        except Exception as e:
            conn.rollback()
            logging.error("Could not replace the file of post %s: %s", post.get("id"), e)
            outcome = "failed"
        stats[outcome] += 1
        if outcome in ("replaced", "md5_refreshed") and post not in replaced:
            replaced.append(post)
        failures_in_a_row = failures_in_a_row + 1 if outcome == "failed" else 0
        if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
            left = len(posts) - index - 1
            logging.error("%d replacements failed in a row; leaving the other %d for the next run",
                          failures_in_a_row, left)
            stats["skipped"] += left
            break

    if replaced:
        # File type, rating and timestamps of these posts, the way the ingest writes them. (It
        # keeps the md5 and file name just written: the rows have a file.)
        for post in replaced:
            post["local_file_path"] = None
        try:
            insert_or_update_posts_bulk(conn, replaced)
        except Exception as e:
            conn.rollback()
            logging.error("Could not refresh the post rows of %d file(s) brought in line with e621: %s",
                          len(replaced), e)
    logging.info("Files changed on e621: %d replaced (old copies kept in %s), %d already current, "
                 "%d skipped, %d failed", stats["replaced"], holding, stats["md5_refreshed"],
                 stats["skipped"], stats["failed"])
    return stats


def _replace_one(conn, post, holding, temp_dir, headers) -> str:
    post_id = post["id"]
    api_md5, url, ext = (_get_file_md5(post) or "").lower(), _get_file_url(post), (_get_file_ext(post) or "").lower()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT file_path, file_name FROM posts WHERE id = %s", (post_id,))
        row = cursor.fetchone()
        current = resolve_existing_file_path(row[0]) if row else None
        if not current or not api_md5:
            return "skipped"     # no local file: the missing-file download handles the post
        cursor.execute("SELECT COUNT(*) FROM posts WHERE file_path = %s AND id <> %s", (row[0], post_id))
        if cursor.fetchone()[0]:
            logging.warning("Post %s changed on e621 but another post records the same file (%s); keeping it",
                            post_id, current)
            return "skipped"

        copies = [current] + [path for path, _ in post_files.find_post_files(conn, [post_id]).get(post_id, [])
                              if _key(path) != _key(current)]
        stem = os.path.splitext(row[1] or os.path.basename(current))[0]

        # A copy that already is e621's current file: nothing to download.
        already = next((path for path in copies if _md5_of(path) == api_md5), None)
        if already:
            if len(copies) > 1:
                others = post_files.move_post_files(conn, [post_id], holding, reason="replaced_on_e621", keep=[already])
                if post_id not in others["cleared"]:
                    logging.warning("Post %s: an older copy could not be moved to %s and stays in the library",
                                    post_id, holding)
            renamed_from, already = already, _with_e621_extension(already, stem, ext)
            cursor.execute(
                "UPDATE posts SET file_path = %s, file_name = %s, md5 = %s, file_url = COALESCE(%s, file_url), "
                "media_hash = NULL WHERE id = %s",
                (normalize_file_path(already), windows_safe_filename(stem + os.path.splitext(already)[1]),
                 api_md5, url, post_id))
            _forget_perceptual_hash(cursor, post_id)
            conn.commit()
            if already != renamed_from:
                post_files.record_rename(conn, renamed_from, already, reason="replaced_on_e621:extension")
            return "md5_refreshed"

        if not url or not ext or ext == "swf":
            logging.info("Post %s changed on e621 but has no downloadable file; keeping the local one", post_id)
            return "skipped"
        guarded = [path for path in copies if protected.is_protected(path)]
        if guarded:
            logging.info("Post %s changed on e621 but has a copy in a protected directory; keeping %s",
                         post_id, guarded[0])
            return "skipped"
        new_name = windows_safe_filename(f"{stem}.{ext}")
        target = os.path.join(os.path.dirname(current), new_name)
        if os.path.exists(target) and _key(target) not in {_key(path) for path in copies}:
            logging.warning("Post %s changed on e621 but another file is already at %s; keeping the local one",
                            post_id, target)
            return "skipped"
        if not _movable(current):
            logging.info("Post %s changed on e621 but its file is in use; trying again next run (%s)", post_id, current)
            return "skipped"

        temp = temp_dir / f"replace_{post_id}.{ext}.part"
        staged = target + STAGED_SUFFIX
        old_mtime = os.stat(current).st_mtime
        moved, placed = None, False
        try:
            _discard(staged)     # left behind by a run that was killed
            _download(url, temp, headers)
            if _md5_of(temp) != api_md5:
                logging.error("Post %s: the downloaded file does not match e621's md5; keeping the local one", post_id)
                return "failed"
            size = os.path.getsize(temp)
            # The slow copy across drives happens while the old file is still in place.
            shutil.move(str(temp), staged)
            try:
                os.utime(staged, (old_mtime, old_mtime))    # the archive step files by modified time
            except OSError:
                pass
            moved = post_files.move_post_files(conn, [post_id], holding, reason="replaced_on_e621")
            if os.path.exists(current) or os.path.exists(target):
                logging.error("Post %s: the old file could not be moved out of the way; keeping it", post_id)
                return "failed"
            _rename_into_place(staged, target)
            placed = True
        except BaseException:      # also Ctrl+C: say where the files are before going down
            if moved and moved["moved"]:
                logging.error("Post %s: the new file was not placed after its old cop%s moved to %s. "
                              "The verified new file is still at %s; rename it to %s to finish by hand.",
                              post_id, "y" if moved["moved"] == 1 else "ies", holding, staged, new_name)
            raise
        finally:
            _discard(temp)
            # Once the old file has left, the staged file is the post's only copy in its folder.
            if not placed and os.path.exists(current):
                _discard(staged)

        if post_id not in moved["cleared"]:
            logging.warning("Post %s: an older copy elsewhere in the library could not be moved to %s", post_id, holding)
        post_files._operation_logger().log_move_operation(
            file_name=new_name, source_path=str(temp), destination_path=target, source_directory=str(temp_dir),
            destination_directory=os.path.dirname(target), file_size=size, reason="replaced_on_e621:new_file")
        cursor.execute(
            "UPDATE posts SET file_path = %s, file_name = %s, md5 = %s, file_url = %s, media_hash = NULL WHERE id = %s",
            (normalize_file_path(target), new_name, api_md5, url, post_id))
        _forget_perceptual_hash(cursor, post_id)
        conn.commit()
        logging.info("Post %s: replaced with e621's current file at %s (%d old cop%s moved to %s)", post_id, target,
                     moved["moved"], "y" if moved["moved"] == 1 else "ies", holding)
        return "replaced"
    finally:
        cursor.close()
