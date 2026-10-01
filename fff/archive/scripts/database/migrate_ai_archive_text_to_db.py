# fff: the __main__ block moved into main(argv); the archive argument defaults to data/ai_generated_posts_archive.txt in fff.
"""Migrate the legacy AI-generated post text archive into MySQL."""

import argparse
import re
import sys
from pathlib import Path

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, create_tables_if_not_exists

POST_BLOCK_RE = re.compile(
    r"Post ID:\s*(?P<id>\d+)\s*\n"
    r"\s*File:\s*(?P<file>.*?)\s*\n"
    r"\s*Path:\s*(?P<path>.*?)\s*\n"
    r"\s*Rating:\s*(?P<rating>.*?)\s*\n"
    r"\s*Deleted:\s*(?P<deleted>.*?)\s*\n"
    r"\s*Ingested:\s*(?P<ingested>.*?)\s*\n"
    r"\s*File Exists:\s*(?P<exists>Yes|No)\s*\n"
    r"\s*Deletion Reason:\s*(?P<reason>.*?)\s*\n",
    re.MULTILINE,
)


def nullable(value):
    value = value.strip()
    return None if not value or value.lower() in {"no file_path", "no file_name"} else value


def artist_name_from_file(file_name):
    if not file_name:
        return None
    match = re.match(r"^(.*?)-(?:safe|questionable|explicit)-\d+(?:\.[^.]+)?$", file_name)
    return match.group(1) if match else None


def parse_archive(path):
    text = path.read_text(encoding="utf-8")
    records = {}
    for match in POST_BLOCK_RE.finditer(text):
        record = match.groupdict()
        post_id = int(record["id"])
        records[post_id] = (
            post_id,
            artist_name_from_file(nullable(record["file"])),
            nullable(record["file"]),
            nullable(record["path"]),
            nullable(record["rating"]),
            nullable(record["reason"]),
            nullable(record["deleted"]),
            nullable(record["ingested"]),
            None,
            record["exists"] == "Yes",
        )
    return records


def migrate(path):
    records = parse_archive(path)
    if not records:
        raise RuntimeError(f"No post records found in {path}")

    config = load_config()
    conn = connect_to_mysql(config.get("mysql", {}))
    if conn is None:
        raise RuntimeError("Could not connect to the configured MySQL database")

    try:
        create_tables_if_not_exists(conn)
        cursor = conn.cursor()
        for record in records.values():
            artist_name = record[1]
            artist_id = None
            if artist_name:
                cursor.execute("SELECT artist_id FROM artists WHERE name = %s", (artist_name,))
                artist_row = cursor.fetchone()
                artist_id = artist_row[0] if artist_row else None
            values = (
                record[0], artist_id, *record[2:]
            )
            cursor.execute(
                """
                INSERT INTO ai_generated_posts_archive
                    (post_id, artist_id, file_name, file_path, rating, deletion_reason,
                     deleted_at, ingested_at, updated_at, file_exists)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    artist_id = VALUES(artist_id),
                    file_name = VALUES(file_name),
                    file_path = VALUES(file_path),
                    rating = VALUES(rating),
                    deletion_reason = VALUES(deletion_reason),
                    deleted_at = VALUES(deleted_at),
                    ingested_at = VALUES(ingested_at),
                    updated_at = VALUES(updated_at),
                    file_exists = VALUES(file_exists)
                """,
                values,
            )
        conn.commit()
        cursor.close()
        print(f"Migrated {len(records)} unique post records into ai_generated_posts_archive.")
    finally:
        conn.close()


def main(argv=None):
    # fff: this was the body of `if __name__ == "__main__":`
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive",
        nargs="?",
        default=archive_paths.AI_ARCHIVE_TXT,  # fff: was data/ai_generated_posts_archive.txt (CWD)
        type=Path,
        help="Legacy text archive path",
    )
    args = parser.parse_args(argv)
    migrate(args.archive)


if __name__ == "__main__":
    main()
