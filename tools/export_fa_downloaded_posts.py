"""Add every FA post the sorter database has a file for to FA Auto-Downloader's downloaded-posts.json.

The sorter tables index the files on disk; FA downloads are named ``<artist>-<rating>-FA-<post id>.<ext>``
(optionally ``-unk-`` and a ``_1`` page suffix), so the post id comes from the file name. Files
downloaded from e621 also count when their e621 post lists an FA submission link as a source
(archive ``source_links``); ``--no-e621`` turns that off. Existing
entries in the JSON are kept as they are; only posts it doesn't list yet are added. The original
file is copied to ``downloaded-posts.json.bak`` before it is rewritten.

    .venv\\Scripts\\python.exe tools\\export_fa_downloaded_posts.py --dry-run
    .venv\\Scripts\\python.exe tools\\export_fa_downloaded_posts.py --check-disk
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fff.common import db  # noqa: E402

DEFAULT_JSON = Path(__file__).resolve().parents[2] / "FA AutoDownloader Files" / "downloaded-posts.json"
ABOUT = ("Submissions downloaded by FurAffinity Auto-Downloader. They will not be downloaded again, even if "
         "the files are moved. Remove an entry (or use Clear in the settings) to allow it again.")
# Views and tracking tables that also have file_name/file_path but don't mean "the file is here".
SKIP_TABLES = {"all_files", "possible_missing_files"}
FA_NAME = re.compile(r"-FA-(\d+)(?:_\d+)?\.[A-Za-z0-9]+$", re.IGNORECASE)
# Submission links only; d.furaffinity.net file links and /user/ pages carry no post id.
FA_LINK = re.compile(r"furaffinity\.net/(?:view|full)/(\d+)", re.IGNORECASE)


def file_tables(cursor):
    cursor.execute("""
        SELECT t.TABLE_NAME FROM information_schema.TABLES t
        WHERE t.TABLE_SCHEMA = DATABASE() AND t.TABLE_TYPE = 'BASE TABLE'
          AND EXISTS (SELECT 1 FROM information_schema.COLUMNS c WHERE c.TABLE_SCHEMA = t.TABLE_SCHEMA
                      AND c.TABLE_NAME = t.TABLE_NAME AND c.COLUMN_NAME = 'file_name')
          AND EXISTS (SELECT 1 FROM information_schema.COLUMNS c WHERE c.TABLE_SCHEMA = t.TABLE_SCHEMA
                      AND c.TABLE_NAME = t.TABLE_NAME AND c.COLUMN_NAME = 'file_path')
        ORDER BY t.TABLE_NAME
    """)
    return [r[0] for r in cursor.fetchall() if r[0] not in SKIP_TABLES]


def iso_utc(value):
    if not isinstance(value, datetime):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    if value.tzinfo is None:
        value = value.astimezone()  # stored as local time
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def add_post(found, post_id, file_name, created):
    downloaded = iso_utc(created)
    # Keep the earliest copy when the same post is in several tables.
    if post_id not in found or downloaded < found[post_id]["downloaded"]:
        found[post_id] = {"url": f"https://www.furaffinity.net/view/{post_id}/",
                          "file": file_name, "downloaded": downloaded}


def e621_fa_sources(e621_ids):
    """Return {e621 post id: [FA post ids]} from the archive's source_links."""
    sources = {}
    if not e621_ids:
        return sources
    conn = db.connect("archive", pooled=False)
    cursor = conn.cursor()
    try:
        ids = sorted(e621_ids)
        for i in range(0, len(ids), 1000):
            chunk = ids[i:i + 1000]
            cursor.execute(f"SELECT post_id, source_link FROM source_links "
                           f"WHERE post_id IN ({','.join(['%s'] * len(chunk))}) AND source_link LIKE %s",
                           (*chunk, "%furaffinity.net/%"))
            for e621_id, link in cursor.fetchall():
                m = FA_LINK.search(link or "")
                if m:
                    fa_ids = sources.setdefault(e621_id, [])
                    fa_id = str(int(m.group(1)))
                    if fa_id not in fa_ids:
                        fa_ids.append(fa_id)
    finally:
        cursor.close()
        conn.close()
    return sources


def collect_posts(check_disk, include_e621):
    """Return {post_id: {url, file, downloaded}} for FA posts the sorter DB has a file for.

    That's FA downloads (post id in the file name) and, with include_e621, e621 files whose
    e621 post lists an FA submission as a source.
    """
    conn = db.connect("sorter", pooled=False)
    cursor = conn.cursor()
    found, per_table, missing = {}, {}, 0
    e621_files = {}  # e621 post id -> [(file_name, created)]
    try:
        for table in file_tables(cursor):
            cursor.execute(f"SELECT file_name, file_path, file_created_at, e621_post_id FROM `{table}` "
                           f"WHERE file_name LIKE %s OR e621_post_id IS NOT NULL", ("%FA-%",))
            count = 0
            for file_name, file_path, created, e621_id in cursor.fetchall():
                m = FA_NAME.search(file_name or "")
                if not m and not (include_e621 and e621_id):
                    continue
                if check_disk and not (file_path and os.path.exists(file_path)):
                    missing += 1
                    continue
                if m:
                    count += 1
                    add_post(found, str(int(m.group(1))), file_name, created)
                else:
                    e621_files.setdefault(e621_id, []).append((file_name, created))
            if count:
                per_table[table] = count
    finally:
        cursor.close()
        conn.close()

    e621_added = 0
    for e621_id, fa_ids in e621_fa_sources(e621_files.keys()).items():
        for file_name, created in e621_files[e621_id]:
            for fa_id in fa_ids:
                if fa_id not in found:
                    e621_added += 1
                add_post(found, fa_id, file_name, created)
    return found, per_table, missing, e621_added


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help=f"default: {DEFAULT_JSON}")
    parser.add_argument("--dry-run", action="store_true", help="report what would be added without writing")
    parser.add_argument("--check-disk", action="store_true", help="skip rows whose file no longer exists on disk")
    parser.add_argument("--no-e621", action="store_true",
                        help="don't add FA sources of downloaded e621 posts, only FA-named files")
    args = parser.parse_args()

    if args.json.exists():
        data = json.loads(args.json.read_text(encoding="utf-8"))
    else:
        data = {"about": ABOUT, "posts": {}}
    posts = data.setdefault("posts", {})

    found, per_table, missing, e621_added = collect_posts(args.check_disk, not args.no_e621)
    for table, n in per_table.items():
        print(f"  {table:<28} {n:>6} FA files")
    if not args.no_e621:
        print(f"  {'e621 files with an FA source':<28} {e621_added:>6} more FA posts")
    if args.check_disk:
        print(f"  skipped {missing} rows whose file is missing on disk")

    new_ids = [pid for pid in found if pid not in posts]
    print(f"{len(found)} FA posts in the database, {len(posts)} already in {args.json.name}, "
          f"{len(new_ids)} to add")
    if args.dry_run or not new_ids:
        if args.dry_run:
            print("Dry run: nothing written.")
        return

    for pid in new_ids:
        posts[pid] = found[pid]
    data["about"] = data.get("about", ABOUT)
    data["updated"] = iso_utc(datetime.now(timezone.utc))
    data["count"] = len(posts)
    data["posts"] = dict(sorted(posts.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0))
    # Keep the extension's key order: about, updated, count, posts.
    ordered = {k: data[k] for k in ("about", "updated", "count", "posts")}
    ordered.update({k: v for k, v in data.items() if k not in ordered})

    if args.json.exists():
        shutil.copy2(args.json, args.json.with_name(args.json.name + ".bak"))
    tmp = args.json.with_name(args.json.name + ".tmp")
    tmp.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, args.json)
    print(f"Wrote {args.json} ({len(posts)} posts).")


if __name__ == "__main__":
    main()
