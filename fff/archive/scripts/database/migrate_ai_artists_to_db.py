# fff: implemented in e621 archive/scripts/database/migrate_ai_artists_to_db.py.
# fff: the __main__ block moved into main(argv); reads data/ai_artist.md in fff.
# fff: main(argv) parses an option-less argparse parser so --help works; the original took no arguments.
"""Import the existing AI artist list into the database ledger."""

import argparse  # fff: for main(argv)
import sys
from pathlib import Path

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, create_tables_if_not_exists, record_ai_flagged_artists
from fff.archive.scripts.favorites.remove_ai_artist_posts import load_ai_artists


def main(argv=None):
    # fff: this was the body of `if __name__ == "__main__":`
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)  # fff: --help only
    artist_file = archive_paths.AI_ARTIST_MD
    artists = load_ai_artists(str(artist_file))
    config = load_config()
    conn = connect_to_mysql(config.get("mysql", {}))
    if conn is None:
        raise RuntimeError("Could not connect to the configured MySQL database")
    try:
        create_tables_if_not_exists(conn)
        count = record_ai_flagged_artists(conn, artists)
        print(f"Imported {count} artist names into ai_flagged_artists.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
