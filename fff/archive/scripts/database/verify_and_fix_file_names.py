# fff: logs go to data/logs/archive instead of ./logs in the CWD.
"""
Verify and Fix file_name / on-disk filename Consistency
=========================================================

Several maintenance processes rename files on disk (e.g. fast_backfill_file_paths.py's
rename_file_to_match_db, which adjusts a filename to preserve the file's real extension)
but historically some of those code paths only updated posts.file_path and never updated
posts.file_name to match. That leaves stale file_name values in e621_posts_db even though
the actual file on disk (and file_path) is correct.

This script is a standalone integrity check + repair pass for every post with a file_path
that points at a real file.

IMPORTANT POLICY: e621 naming convention ("{artist}-{rating}-{postid}.{ext}") always wins.
Some posts' file_path points at a file that was reused via MD5/perceptual-hash dedup from a
different source (e.g. a FurAffinity or Twitter import) and was never actually renamed to
e621 convention. For those, this script does NOT just copy the foreign filename into the
file_name column - it renames the physical file to the correct e621-style name (a plain
os.rename, which does not change modified/created timestamps) and updates file_path and
file_name together, the same way check_and_update_file_artist_names does.

Only when the on-disk name is ALREADY valid e621 convention for that post (correct post id,
a real rating word, no foreign-source markers) but merely disagrees with the file_name column
(e.g. extension or artist tags changed) does this script just correct the DB column without
touching the file.

This does NOT decide artist attribution beyond what's already in posts_artists (that's
check_and_update_file_artist_names / update_artist_filenames.py's job when artist tags
change). It only fixes cases where file_name has drifted from the file that is actually on
disk.

Usage:
    python verify_and_fix_file_names.py                  # Report + fix mismatches
    python verify_and_fix_file_names.py --dry-run         # Report only, no changes
    python verify_and_fix_file_names.py --batch-limit N   # Only check N posts (for testing)
    python verify_and_fix_file_names.py --include-deleted # Also check deleted posts
"""

import argparse
import logging
import os
import sys

from fff.archive import paths as archive_paths

from fff.archive.config import load_config, normalize_file_path
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql, get_post_artists

RATING_MAP = {"s": "safe", "q": "questionable", "e": "explicit"}
RATING_WORDS = {"safe", "questionable", "explicit"}
# Markers used elsewhere in this codebase (check_and_update_file_artist_names) to identify
# files imimplemented in a non-e621 source and named with that source's own convention.
FOREIGN_SOURCE_MARKERS = ("-fa-", "-da-", "-ib-", "-tw-", "-pixiv-")
EXCLUDED_ARTISTS = {"avoid_posting", "conditional_dnp", "unknown", "sound_warning"}


def is_valid_e621_name(name, post_id):
    """Check whether `name` already follows e621 convention for this specific post:
    ends in "-{rating}-{post_id}.{ext}" and carries no known foreign-source marker."""
    if not name:
        return False
    if any(marker in name.lower() for marker in FOREIGN_SOURCE_MARKERS):
        return False
    base = name.rsplit('.', 1)[0]
    parts = base.split('-')
    if len(parts) < 3:
        return False
    if parts[-1] != str(post_id):
        return False
    if parts[-2].lower() not in RATING_WORDS:
        return False
    return True


def build_e621_file_name(conn, post_id, rating, extension):
    """Compute the canonical e621-style file name for a post, the same way
    check_and_update_file_artist_names does: {sorted artists}-{rating}-{post_id}.{ext}."""
    artists = get_post_artists(conn, post_id) or []
    artists = [a for a in artists if a not in EXCLUDED_ARTISTS]
    if not artists:
        artists = ["unknown"]
    rating_full = RATING_MAP.get((rating or "unknown").lower(), (rating or "unknown").lower())
    name = f"{'_'.join(sorted(artists))}-{rating_full}-{post_id}"
    if extension:
        name = f"{name}.{extension}"
    return name


def verify_and_fix_file_names(conn, batch_limit=None, dry_run=False, include_deleted=False, commit_every=500):
    """
    Ensure posts.file_name and the physical file at posts.file_path agree, preferring
    e621 naming convention over whatever a dedup-reused foreign file happens to be called.

    Args:
        conn: Database connection to e621_posts_db
        batch_limit: Optional limit on number of posts to check
        dry_run: If True, only report what would change without making changes
        include_deleted: If True, also check deleted posts
        commit_every: Commit to the database after this many fixes

    Returns:
        dict with statistics
    """
    cursor = conn.cursor()
    stats = {
        "checked": 0,
        "ok": 0,
        "column_fixed": 0,
        "renamed_to_e621": 0,
        "no_file_name": 0,
        "collision_skipped": 0,
        "missing_file": 0,
    }

    query = """
        SELECT id, file_path, file_name, rating
        FROM posts
        WHERE file_path IS NOT NULL
        AND file_path != ''
    """
    if not include_deleted:
        query += " AND deleted = 0"
    query += " ORDER BY id"
    if batch_limit:
        query += f" LIMIT {int(batch_limit)}"

    cursor.execute(query)
    posts = cursor.fetchall()
    cursor.close()

    if not posts:
        logging.info("No posts with file_path to check")
        return stats

    logging.info(f"Checking {len(posts)} posts for file_name/on-disk consistency...")

    update_cursor = conn.cursor()
    pending_commits = 0

    for post_id, file_path, file_name, rating in posts:
        stats["checked"] += 1

        if not os.path.exists(file_path):
            stats["missing_file"] += 1
            logging.debug(f"Post {post_id}: file_path does not exist on disk, skipping: {file_path}")
            continue

        actual_name = os.path.basename(file_path)

        if is_valid_e621_name(actual_name, post_id):
            # The physical file is already named correctly per e621 convention - just make
            # sure the DB column agrees (it can drift without a rename, e.g. extension or
            # artist tag changes recomputing the expected string).
            if file_name == actual_name:
                stats["ok"] += 1
                continue

            if not file_name:
                stats["no_file_name"] += 1
                log_prefix = "[DRY RUN] " if dry_run else ""
                logging.info(f"{log_prefix}Post {post_id}: file_name is empty, {'would set' if dry_run else 'setting'} to '{actual_name}'")
            else:
                stats["column_fixed"] += 1
                if dry_run:
                    logging.info(f"[DRY RUN] Post {post_id}: file_name column out of sync - DB says '{file_name}', disk has '{actual_name}' -> would fix column")
                else:
                    logging.info(f"Post {post_id}: file_name column out of sync - DB said '{file_name}', disk has '{actual_name}' -> fixed column")

            if not dry_run:
                update_cursor.execute(
                    "UPDATE posts SET file_name = %s WHERE id = %s",
                    (actual_name, post_id)
                )
                pending_commits += 1
            continue

        # File on disk does NOT match e621 convention - likely a dedup-reused file from a
        # foreign source (FurAffinity/Twitter/etc.) or some other stray name. e621 naming
        # wins: rename the file rather than adopting whatever name is currently on disk.
        _, ext = os.path.splitext(actual_name)
        expected_name = build_e621_file_name(conn, post_id, rating, ext.lstrip('.'))

        directory = os.path.dirname(file_path)
        new_path = os.path.join(directory, expected_name)

        if os.path.exists(new_path) and new_path != file_path:
            stats["collision_skipped"] += 1
            logging.warning(
                f"Post {post_id}: disk file '{actual_name}' doesn't match e621 convention "
                f"and target '{expected_name}' already exists - skipping, needs manual review"
            )
            continue

        if dry_run:
            logging.info(f"[DRY RUN] Post {post_id}: disk file '{actual_name}' doesn't match e621 convention -> would rename to '{expected_name}'")
            stats["renamed_to_e621"] += 1
            continue

        try:
            os.rename(file_path, new_path)
        except Exception as e:
            logging.error(f"Post {post_id}: failed to rename '{file_path}' -> '{new_path}': {e}")
            continue

        try:
            update_cursor.execute(
                "UPDATE posts SET file_path = %s, file_name = %s WHERE id = %s",
                (normalize_file_path(new_path), expected_name, post_id)
            )
            pending_commits += 1
            stats["renamed_to_e621"] += 1
            logging.info(f"Post {post_id}: renamed non-e621 file '{actual_name}' -> '{expected_name}'")
        except Exception as db_error:
            logging.error(f"Post {post_id}: DB update failed ({db_error}), rolling back file rename")
            try:
                os.rename(new_path, file_path)
            except Exception:
                pass

        if pending_commits >= commit_every:
            conn.commit()
            pending_commits = 0
            logging.info(f"Progress: {stats['checked']}/{len(posts)} checked...")

    if not dry_run and pending_commits > 0:
        conn.commit()

    update_cursor.close()

    logging.info("File name verification complete:")
    logging.info(f"  Checked: {stats['checked']}")
    logging.info(f"  Already correct: {stats['ok']}")
    logging.info(f"  file_name column fixed (no rename needed): {stats['column_fixed']}")
    logging.info(f"  Renamed to e621 convention: {stats['renamed_to_e621']}")
    logging.info(f"  Empty file_name (now set): {stats['no_file_name']}")
    logging.info(f"  Skipped (target name collision): {stats['collision_skipped']}")
    logging.info(f"  Skipped (file not found on disk): {stats['missing_file']}")

    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify and fix posts.file_name / on-disk filename, preferring e621 naming convention",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without making them")
    parser.add_argument("--batch-limit", type=int, help="Only check N posts (useful for testing)")
    parser.add_argument("--include-deleted", action="store_true", help="Also check deleted posts")
    args = parser.parse_args(argv)

    setup_logging(str(archive_paths.LOG_DIR))  # fff: was ./logs (CWD)
    config = load_config()

    logging.info("=" * 80)
    logging.info("FILE NAME VERIFICATION & REPAIR")
    logging.info("=" * 80)

    if args.dry_run:
        logging.info("DRY RUN MODE - No changes will be made")
    if args.include_deleted:
        logging.info("INCLUDING DELETED POSTS")

    conn = connect_to_mysql(config["mysql"])
    if not conn:
        logging.error("Failed to connect to database")
        sys.exit(1)

    try:
        stats = verify_and_fix_file_names(
            conn,
            batch_limit=args.batch_limit,
            dry_run=args.dry_run,
            include_deleted=args.include_deleted,
        )

        logging.info("=" * 80)
        logging.info("SUMMARY")
        logging.info("=" * 80)
        logging.info(f"Posts checked: {stats['checked']}")
        logging.info(f"Already correct: {stats['ok']}")
        logging.info(f"file_name column fixed: {stats['column_fixed']}")
        logging.info(f"Renamed to e621 convention: {stats['renamed_to_e621']}")
        logging.info(f"Empty file_name set: {stats['no_file_name']}")
        logging.info(f"Skipped, target name collision: {stats['collision_skipped']}")
        logging.info(f"Skipped, file missing on disk: {stats['missing_file']}")

        if args.dry_run:
            logging.info("\nDRY RUN - No changes were made")
        else:
            fixed_total = stats['column_fixed'] + stats['renamed_to_e621'] + stats['no_file_name']
            logging.info(f"\nSuccessfully fixed {fixed_total} posts")

    except Exception as e:
        logging.error(f"Error during file name verification: {e}", exc_info=True)
        sys.exit(1)
    finally:
        conn.close()
        logging.info("Database connection closed")


if __name__ == "__main__":
    main()
