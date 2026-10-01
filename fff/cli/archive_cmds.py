"""``fff e621 ...``, ``fff fa ...``, ``fff gui ...``, ``fff web`` and archive ``fff db``/``fff legacy`` commands.

Commands are registered here with their help text and argument handling.

* Commands with their own argparse get the rest of the command line as-is.
* Commands without argparse get their options declared here instead. Unknown options are
  rejected, so ``--help`` can never start a real run.
"""
from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass, field

FORWARD = None  # the script parses its own arguments


@dataclass
class Command:
    path: tuple[str, ...]            # e.g. ("e621", "backfill", "pools")
    module: str                      # e.g. "fff.archive.scripts.database.backfill_pools"
    help: str
    func: str = "main"
    options: list | None = FORWARD   # [(flags, kwargs), ...] for scripts without argparse
    call: str = "argv"               # "argv": func(argv) | "none": func()
    extra: dict = field(default_factory=dict)


S = "fff.archive.scripts"
COMMANDS = [
    # --- sync ------------------------------------------------------------------
    Command(("e621", "sync"), "fff.archive.sync", "Sync e621 favorites into e621_posts_db (full pipeline)"),
    # --- backfill / verify -----------------------------------------------------------
    Command(("e621", "backfill", "deletion-reasons"), f"{S}.database.backfill_deletion_reasons",
            "Scrape deletion reasons and takedown details for deleted posts"),
    Command(("e621", "backfill", "file-paths"), f"{S}.database.backfill_file_paths_from_image_sorting",
            "Fill missing posts.file_path from the image_sorting_hashes database"),
    Command(("e621", "backfill", "file-paths-fast"), f"{S}.database.fast_backfill_file_paths",
            "Bulk file_path backfill/verify via the all_files view"),
    Command(("e621", "backfill", "pools"), f"{S}.database.backfill_pools",
            "Pool links from cached pages, plus pool metadata from the API"),
    Command(("e621", "backfill", "relationships"), f"{S}.database.backfill_post_relationships",
            "Parent/child relationships from cached pages"),
    Command(("e621", "backfill", "phash-cache"), f"{S}.database.build_perceptual_hash_cache",
            "Hash local files into e621_perceptual_hashes"),
    Command(("e621", "verify", "post-ids"), f"{S}.database.validate_post_ids_in_filenames",
            "Check post ids in filenames against the DB (with a review GUI)"),
    Command(("e621", "rename-artists"), f"{S}.database.update_artist_filenames",
            "Rename files whose artist tags changed"),
    Command(("e621", "report", "deletion-timestamps"), f"{S}.database.check_deletion_timestamp_accuracy",
            "Report how accurate deleted_at timestamps are"),
    Command(("e621", "report", "takedowns"), f"{S}.database.view_takedown_details",
            "Interactive takedown viewer"),
    Command(("e621", "ai", "cleanup-deleted"), f"{S}.database.cleanup_ai_generated_posts",
            "Posts e621 deleted as AI: snapshot, unfavorite, optionally trash files, delete from DB (dry run by default)"),
    Command(("legacy", "add-deletion-reason-column"), f"{S}.database.add_deletion_reason_column",
            "One-off migration: add posts.deletion_reason"),
    Command(("legacy", "migrate-ai-archive-text"), f"{S}.database.migrate_ai_archive_text_to_db",
            "One-off: import data/ai_generated_posts_archive.txt into the DB"),
    Command(("legacy", "migrate-ai-artists"), f"{S}.database.migrate_ai_artists_to_db",
            "One-off: import data/ai_artist.md into ai_flagged_artists"),
    Command(("e621", "verify", "file-paths"), f"{S}.database.verify_and_repair_file_paths",
            "Verify posts.file_path and repair broken paths"),
    Command(("e621", "verify", "file-names"), f"{S}.database.verify_and_fix_file_names",
            "Make posts.file_name agree with the files on disk"),
    Command(("e621", "match-downloads"), f"{S}.database.create_download_tables_and_match_files",
            "Build dl_fav/dl_nsfw/dl_sfw tables and match downloaded files to posts",
            options=[(("--dry-run",), {"action": "store_true"}), (("--skip-to-id-matching",), {"action": "store_true"})]),
    # --- AI cleanup ------------------------------------------------------------------
    Command(("e621", "ai", "remove-tagged"), f"{S}.favorites.remove_ai_tagged_posts",
            "Remove posts with AI tags (move file, unfavorite, downvote, delete from DB)"),
    Command(("e621", "ai", "remove-artists"), f"{S}.favorites.remove_ai_artist_posts",
            "Remove posts by artists listed in data/ai_artist.md"),
    # --- favorites ---------------------------------------------------------------------
    Command(("e621", "favorites", "add-missing"), f"{S}.favorites.favorite_missing_posts",
            "Favorite e621 posts the sorter knows about but that aren't favorited, then sync"),
    Command(("e621", "favorites", "swap-duplicates"), f"{S}.favorites.swap_duplicate_favorites",
            "Move favorites from 'inferior/duplicate of' posts to their replacements"),
    Command(("e621", "favorites", "from-md5"), f"{S}.favorites.md5_favorite_checker",
            "Look up md5s from data/md5_search/md5.txt and favorite the matches"),
    # --- files --------------------------------------------------------------------------
    Command(("e621", "files", "find-deleted"), f"{S}.file_management.check_deleted_posts_files_enhanced",
            "Look for local files of deleted posts (checks up to 10 posts)", options=[]),
    Command(("e621", "files", "unfavorited"), f"{S}.file_management.manage_unfavorited_posts",
            "Interactive menu for unfavorited posts (archive / delete)", options=[]),
    Command(("e621", "files", "restore"), f"{S}.file_management.restore_deleted_files",
            "Restore files listed in a deleted-files CSV (one-off tool)"),
    # --- reports ------------------------------------------------------------------------
    Command(("e621", "report", "favorites-diff"), f"{S}.favorites.analyze_favorites_difference",
            "Compare database post ids with live favorites", options=[]),
    Command(("e621", "report", "relationships"), f"{S}.tools.check_relationship_stats",
            "Print parent/child relationship statistics", options=[]),
    Command(("e621", "db-export"), f"{S}.tools.download_and_process_e621_db_exports",
            "Download e621 DB exports and load them into e621_db_dump_* tables", options=[]),
    # --- db / tools ------------------------------------------------------------------
    Command(("e621", "reset-db"), f"{S}.database.reset_database", "Drop all e621_posts_db tables (asks to confirm)"),
    Command(("db", "inspect-hashes"), f"{S}.tools.inspect_image_sorting_hashes_db",
            "Print the image_sorting_hashes schema and hash columns", options=[], call="none"),
        # --- source search ------------------------------------------------------------------
    Command(("fluffle", "search"), "fff.fluffle.search",
            "Reverse-search images with reverse search (+FuzzySearch), process e621/FA matches, sort the rest",
            options=[]),
    Command(("fluffle", "process-e621"), "fff.fluffle.e621_matches",
            "Process stored exact e621 matches: favorite, download, move existing code (applies the blacklist)",
            options=[]),
    Command(("fluffle", "process-fa"), "fff.fluffle.fa_matches",
            "Process stored exact FA matches (no e621 match): favorite, download, move existing code",
            options=[]),
    Command(("fluffle", "sync-fa-favorites"), "fff.fluffle.sync_fa_favorites",
            "Favorite on FA the images that matched both e621 and FA", options=[]),
    Command(("fluffle", "verify-db"), "fff.fluffle.verify_database", "Print recent rows and counts from image_search",
            func="verify_database", options=[]),
    Command(("fa", "rename-authors"), "fff.fluffle.rename_fa_authors",
            "Rename *-FA-* files from author name to author id (mappings from the search database; interactive)",
            options=[]),
    Command(("fa", "rename-authors-manual"), "fff.fluffle.rename_fa_authors_manual",
            "Same, with mappings from config/fa_author_mappings.yaml (interactive)", options=[]),
    # --- FFF -----------------------------------------------------------------------
    Command(("sort",), "fff.sorter.entry",
            "Sort and archive files, organize tags and artists, maintain the library, and find duplicates"),
    Command(("e621", "fix-no-source-names"), "fff.sorter.scripts.fix_no_source_filenames",
            "Rename files whose path name differs from posts.file_name (excludes Fav/Artists, Tag Sorted, ...)",
            options=[(("--dry-run",), {"action": "store_true"})]),
    Command(("dupes", "advanced"), "fff.sorter.scripts.find_duplicates_advanced",
            "Multi-stage duplicate finder (currently broken)"),
    Command(("db", "report", "space"), "fff.sorter.scripts.table_space_report",
            "Sum file_size per image_sorting_hashes table", options=[]),
    Command(("db", "report", "archive-tables"), "fff.sorter.scripts.check_archive_tables",
            "List the archive_* tables", options=[]),
    Command(("db", "diagnose", "all-files-view"), "fff.sorter.scripts.diagnose_all_files_view",
            "Diagnose the all_files view (artist-scan filters)", options=[]),
    Command(("db", "diagnose", "op-logging"), "fff.sorter.scripts.diagnose_file_operations_logging",
            "Exercise file_operations logging (writes and deletes test rows)", options=[]),
    Command(("legacy", "migrate-artist-columns"), "fff.legacy.sorter.migrate_artist_columns",
            "One-off: add last_artist_check / artist_matched to every table", options=[]),
    Command(("legacy", "migrate-paths-forward-slashes"), "fff.legacy.sorter.migrate_paths_to_forward_slashes",
            "One-off: rewrite stored file_path values with forward slashes"),
    Command(("legacy", "fix-file-hash-column"), "fff.legacy.sorter.fix_file_hash_column",
            "One-off: widen file_operations.file_hash (obsolete)", options=[]),
    Command(("legacy", "remove-file-hash-column"), "fff.legacy.sorter.remove_file_hash_column",
            "One-off: drop file_operations.file_hash", options=[]),
    Command(("hash",), "fff.sorter.hash_cli", "Hash files / show info / find duplicates (v1 media_hasher CLI)"),
    Command(("gui", "duplicates"), "fff.gui.duplicate_manager_ui", "Duplicate manager GUI", options=[]),
    Command(("gui", "tag-sort-config"), "fff.gui.tag_sort_config_ui_v2", "Tag sorting rule editor GUI"),
    Command(("gui", "artist-config"), "fff.gui.artist_config_ui", "Artist config editor GUI (sort_artists.yaml)"),
    # --- FA --------------------------------------------------------------------------
    Command(("fa", "sync-external"), "fff.fluffle.sync_external_fa_sources",
            "Track FA source links from e621 posts and favorite them on FA", options=[], call="none"),
    # --- GUIs --------------------------------------------------------------------------
    Command(("gui", "blacklist-review"), "fff.gui.blacklist_review_ui",
            "Review blacklisted posts (auto-deletes critical/AI-tagged posts first)", options=[]),
    Command(("gui", "artist-review"), "fff.gui.artists_review_ui", "Review posts by artists from review_artists.yaml",
            options=[]),
    Command(("web",), "fff.gui.archive_browser", "Local web gallery for the archive (Flask)"),
]


GROUP_HELP = {
        ("e621",): "Sync favorites, backfill and verify data, clean up AI-tagged posts, and generate reports",
    ("e621", "backfill"): "Fill in missing data (deletion reasons, file paths, pools, relationships, hashes)",
    ("e621", "verify"): "Check and repair file paths, file names and post ids",
    ("e621", "report"): "Read-only reports",
    ("e621", "ai"): "Remove AI-generated content",
    ("e621", "favorites"): "Add, swap or look up favorites",
    ("e621", "files"): "Local files of deleted/unfavorited posts",
    ("fa",): "FurAffinity tools",
        ("fluffle",): "Reverse-image source search and match processing",
    ("gui",): "Desktop GUIs",
        ("legacy",): "One-off database and data migrations",
    ("dupes",): "Duplicate finders",
    ("db", "report"): "Read-only database reports",
    ("db", "diagnose"): "Database diagnostics",
}


def _run(command: Command, args) -> int:
    module = importlib.import_module(command.module)
    func = getattr(module, command.func)
    if command.options is FORWARD:
        argv = list(args.rest)
    else:
        argv = []
        for flags, kwargs in command.options:
            dest = flags[0].lstrip("-").replace("-", "_")
            value = getattr(args, dest)
            if kwargs.get("action") == "store_true":
                if value:
                    argv.append(flags[0])
            elif value is not None:
                argv += [flags[0], str(value)]
    import sys

    saved_argv0 = sys.argv[0] if sys.argv else ""
    if sys.argv:
        sys.argv[0] = "fff " + " ".join(command.path)  # argparse's prog in the script's --help
    try:
        result = func(argv) if command.call == "argv" else func()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        if sys.argv:
            sys.argv[0] = saved_argv0
    return result if isinstance(result, int) else 0


def forwarded_argv(argv: list[str], command_path: tuple[str, ...]) -> list[str]:
    """Everything after the command's own words, e.g. ``fff --env test e621 sync -x`` -> ``["-x"]``.

    argparse's REMAINDER won't capture a leading ``--option`` after a subcommand, so forwarded
    commands take their arguments straight from argv instead.
    """
    n = len(command_path)
    for i in range(len(argv) - n + 1):
        if tuple(argv[i:i + n]) == command_path:
            return list(argv[i + n:])
    return []


def register(sub) -> None:
    """Add every command to the top-level subparsers ``sub``."""
    groups: dict[tuple, argparse._SubParsersAction] = {(): sub}
    parsers: dict[tuple, argparse.ArgumentParser] = {}
    existing = {name: p for name, p in sub.choices.items()}
    for command in COMMANDS:
        for depth in range(1, len(command.path)):
            prefix = command.path[:depth]
            if prefix not in groups:
                parent = groups[prefix[:-1]]
                parser = existing.get(prefix[0]) if depth == 1 else None
                if parser is None:
                    parser = parent.add_parser(prefix[-1], help=GROUP_HELP.get(prefix, f"{' '.join(prefix)} commands"))
                    parsers[prefix] = parser
                action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
                if action is None:
                    action = parser.add_subparsers(dest="_".join(prefix) + "_command", metavar="<command>", required=True)
                groups[prefix] = action
        parent = groups[command.path[:-1]]
        description = command.help
        if command.options is FORWARD:
            p = parent.add_parser(command.path[-1], help=command.help, add_help=False, description=description,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
            p.add_argument("rest", nargs=argparse.REMAINDER, help="arguments for this command (try --help)")
            p.set_defaults(forward_path=command.path)
        else:
            p = parent.add_parser(command.path[-1], help=command.help, description=description,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
            for flags, kwargs in command.options:
                p.add_argument(*flags, **kwargs)
        p.set_defaults(func=lambda args, c=command: _run(c, args))
