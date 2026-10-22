"""Unfavorite on FurAffinity the FA sources of e621 posts being removed from favorites.

Call unfavorite_fa_sources() before the posts are deleted from the DB: it reads their
source_links rows. An FA submission is left alone while another post in the DB still
links to it.

Submissions are queued in fa_pending_unfavorites before FA is contacted and leave the
queue once unfavorited, so a failure (FA down, expired cookies, ...) is retried by
retry_pending_fa_unfavorites() on a later sync after the e621 post itself is gone.
"""
import logging
import re
import time

import mysql.connector

from fff.clients.furaffinity import FASubmissionNotFoundError, get_firefox_cookies, remove_from_fa_favorites
from fff.common import config as fff_config

FA_SUBMISSION_RE = re.compile(r'furaffinity\.net/(?:view|full)/(\d+)', re.IGNORECASE)
FA_REQUEST_DELAY = 2.0  # FA is strict about rate limiting
DEFAULT_MAX_PER_RUN = 100
MAX_ATTEMPTS = 10  # a queued submission stops being retried after this many failures
MAX_CONSECUTIVE_FAILURES = 3  # FA is likely blocking us; leave the rest queued for next time

_cookies = None
_table_ready = False


def _fa_cookies():
    """Load the Firefox FA cookies once per process."""
    global _cookies
    if _cookies is None:
        _cookies = get_firefox_cookies()
    return _cookies


def _ensure_pending_table(conn):
    """Create fa_pending_unfavorites once per process (the GUI never runs create_tables_if_not_exists)."""
    global _table_ready
    if _table_ready:
        return
    cursor = conn.cursor()
    try:
        cursor.execute("""CREATE TABLE IF NOT EXISTS fa_pending_unfavorites (
            fa_post_id VARCHAR(20) PRIMARY KEY,
            attempts INT NOT NULL DEFAULT 0,
            last_error VARCHAR(255) DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_attempt_at DATETIME DEFAULT NULL
        );""")
        conn.commit()
        _table_ready = True
    finally:
        cursor.close()


def _linked_elsewhere(cursor, fa_id, exclude_post_ids):
    """True if a post outside exclude_post_ids still has this FA submission as a source."""
    pattern = rf'furaffinity\.net/(view|full)/{fa_id}([^0-9]|$)'
    if exclude_post_ids:
        placeholders = ','.join(['%s'] * len(exclude_post_ids))
        cursor.execute(
            f"SELECT 1 FROM source_links WHERE post_id NOT IN ({placeholders}) "
            "AND source_link REGEXP %s LIMIT 1",
            list(exclude_post_ids) + [pattern],
        )
    else:
        cursor.execute("SELECT 1 FROM source_links WHERE source_link REGEXP %s LIMIT 1", (pattern,))
    return cursor.fetchone() is not None


def fa_ids_for_posts(conn, post_ids):
    """FA submission ids sourced by these posts that no other post in the DB links to."""
    post_ids = list(post_ids)
    if not post_ids:
        return []

    placeholders = ','.join(['%s'] * len(post_ids))
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT source_link FROM source_links WHERE post_id IN ({placeholders}) "
            "AND source_link LIKE '%%furaffinity.net/%%'",
            post_ids,
        )
        fa_ids = sorted({m.group(1) for (link,) in cursor.fetchall()
                         if (m := FA_SUBMISSION_RE.search(link))})

        unshared = []
        for fa_id in fa_ids:
            if _linked_elsewhere(cursor, fa_id, post_ids):
                logging.info("Keeping FA favorite %s: another post still links to it", fa_id)
            else:
                unshared.append(fa_id)
        return unshared
    finally:
        cursor.close()


def _mark_unfavorited(fa_ids):
    """Record the removals in the reverse search DB's external_fa_sources tracking table."""
    if not fa_ids:
        return
    db_config = fff_config.legacy_view("fluffle").get('database', {})
    try:
        conn = mysql.connector.connect(
            host=db_config.get('host', 'localhost'),
            user=db_config.get('user'),
            password=db_config.get('password'),
            database=db_config.get('database'),
        )
    except mysql.connector.Error as e:
        logging.warning("Could not update external_fa_sources: %s", e)
        return
    try:
        cursor = conn.cursor()
        placeholders = ','.join(['%s'] * len(fa_ids))
        cursor.execute(
            f"UPDATE external_fa_sources SET is_favorited = FALSE, last_checked = NOW(), "
            f"error_message = 'Unfavorited: e621 post removed' WHERE post_id IN ({placeholders})",
            list(fa_ids),
        )
        conn.commit()
        cursor.close()
    except mysql.connector.Error as e:
        logging.warning("Could not update external_fa_sources: %s", e)
    finally:
        conn.close()


def _unfavorite_queued(conn, fa_ids):
    """Unfavorite queued FA submissions, removing each from the queue once done."""
    cookies = _fa_cookies()
    if not cookies:
        logging.warning("FA unfavorite postponed for %d submission(s): no FurAffinity cookies in Firefox",
                        len(fa_ids))
        return 0

    logging.info("Unfavoriting %d FA submission(s): %s", len(fa_ids), ', '.join(fa_ids))
    removed = []
    done = []
    consecutive_failures = 0
    cursor = conn.cursor()
    try:
        for i, fa_id in enumerate(fa_ids):
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logging.warning("Stopping FA unfavorites after %d failures in a row; %d left queued for the next sync",
                                consecutive_failures, len(fa_ids) - i)
                break
            if i:
                time.sleep(FA_REQUEST_DELAY)
            try:
                ok = remove_from_fa_favorites(fa_id, cookies)
            except FASubmissionNotFoundError:
                logging.info("FA submission %s no longer exists; nothing to unfavorite", fa_id)
                done.append(fa_id)
                consecutive_failures = 0
                continue

            if ok:
                logging.info("Unfavorited FA submission %s", fa_id)
                removed.append(fa_id)
                done.append(fa_id)
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                logging.warning("Failed to unfavorite FA submission %s; queued for retry", fa_id)
                cursor.execute(
                    "UPDATE fa_pending_unfavorites SET attempts = attempts + 1, last_attempt_at = NOW(), "
                    "last_error = 'unfavorite request failed' WHERE fa_post_id = %s",
                    (fa_id,),
                )
                conn.commit()

        if done:
            placeholders = ','.join(['%s'] * len(done))
            cursor.execute(f"DELETE FROM fa_pending_unfavorites WHERE fa_post_id IN ({placeholders})", done)
            conn.commit()
    finally:
        cursor.close()

    _mark_unfavorited(removed)
    return len(removed)


def unfavorite_fa_sources(conn, post_ids, e621_config):
    """Unfavorite on FA the submissions sourced by these e621 posts. Never raises.

    Controlled by archive.e621.unfavorite_fa_sources (default true). Skips entirely when a
    batch would touch more than archive.e621.fa_unfavorite_max_per_run submissions, so a
    bad favorites fetch can't wipe out the FA favorites. Failures stay queued for
    retry_pending_fa_unfavorites(). Returns the number unfavorited.
    """
    if not e621_config.get("unfavorite_fa_sources", True):
        return 0
    try:
        fa_ids = fa_ids_for_posts(conn, post_ids)
        if not fa_ids:
            return 0

        max_per_run = e621_config.get("fa_unfavorite_max_per_run", DEFAULT_MAX_PER_RUN)
        if max_per_run is not None and len(fa_ids) > max_per_run:
            logging.warning("Skipping FA unfavorite: %d FA submissions exceeds the limit of %d "
                            "(archive.e621.fa_unfavorite_max_per_run)", len(fa_ids), max_per_run)
            return 0

        # Queue first so nothing is lost once the e621 posts (and their source_links) are deleted
        _ensure_pending_table(conn)
        cursor = conn.cursor()
        try:
            cursor.executemany("INSERT IGNORE INTO fa_pending_unfavorites (fa_post_id) VALUES (%s)",
                               [(fa_id,) for fa_id in fa_ids])
            conn.commit()
        finally:
            cursor.close()

        return _unfavorite_queued(conn, fa_ids)
    except Exception as e:
        logging.error("FA unfavorite failed: %s", e)
        return 0


def retry_pending_fa_unfavorites(conn, e621_config):
    """Retry queued FA unfavorites that failed on an earlier run. Never raises.

    A submission is dropped from the queue instead if a post in the DB links to it again
    (re-favorited on e621). Returns the number unfavorited.
    """
    if not e621_config.get("unfavorite_fa_sources", True):
        return 0
    try:
        _ensure_pending_table(conn)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT fa_post_id FROM fa_pending_unfavorites WHERE attempts < %s "
                           "ORDER BY created_at", (MAX_ATTEMPTS,))
            pending = [row[0] for row in cursor.fetchall()]
            if not pending:
                return 0

            fa_ids = []
            for fa_id in pending:
                if _linked_elsewhere(cursor, fa_id, []):
                    logging.info("Dropping queued FA unfavorite %s: a post links to it again", fa_id)
                    cursor.execute("DELETE FROM fa_pending_unfavorites WHERE fa_post_id = %s", (fa_id,))
                else:
                    fa_ids.append(fa_id)
            conn.commit()
        finally:
            cursor.close()

        if not fa_ids:
            return 0
        logging.info("Retrying %d queued FA unfavorite(s)", len(fa_ids))
        return _unfavorite_queued(conn, fa_ids)
    except Exception as e:
        logging.error("FA unfavorite retry failed: %s", e)
        return 0
