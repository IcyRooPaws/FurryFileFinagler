# fff command reference

Generated from the parsers by `python tools/gen_commands_doc.py`; do not edit by hand.
Global options: `fff [--config PATH] [--env prod|test] <command>`.

## General commands

| Command | What it does |
|---|---|
| `fff config make-example` | Write config/config.example.yaml from config.yaml, sanitized |
| `fff config test-dirs` | Show where each configured folder goes in the test environment |
| `fff config show` | Print the config (secrets redacted) |
| `fff db copy-schema` | Create/refresh the test_* databases as empty copies of production's structure |
| `fff db copy-data` | Replace the test_* databases with a full copy of production (schema + rows) |
| `fff db rewrite-test-paths` | Point file paths stored in the test_* databases at the test tree (E:/Testing/...) |
| `fff setup` | Open the guided configuration and first-run checks |
| `fff doctor` | Check databases, folders and dependencies |

## Archive and Media Commands

| Command | What it does |
|---|---|
| `fff e621 sync` | Sync e621 favorites into e621_posts_db (full pipeline) |
| `fff e621 backfill deletion-reasons` | Scrape deletion reasons and takedown details for deleted posts |
| `fff e621 backfill file-paths` | Fill missing posts.file_path from the image_sorting_hashes database |
| `fff e621 backfill file-paths-fast` | Bulk file_path backfill/verify via the all_files view |
| `fff e621 backfill pools` | Pool links from cached pages, plus pool metadata from the API |
| `fff e621 backfill relationships` | Parent/child relationships from cached pages |
| `fff e621 backfill phash-cache` | Hash local files into e621_perceptual_hashes |
| `fff e621 verify post-ids` | Check post ids in filenames against the DB (with a review GUI) |
| `fff e621 rename-artists` | Rename files whose artist tags changed |
| `fff e621 report deletion-timestamps` | Report how accurate deleted_at timestamps are |
| `fff e621 report takedowns` | Interactive takedown viewer |
| `fff e621 ai cleanup-deleted` | Posts e621 deleted as AI: snapshot, flag artist, unfavorite, optionally trash files, delete from DB (dry run by default; sync runs it with files trashed) |
| `fff legacy migrate-ai-artists` | One-off: import data/ai_artist.md into ai_flagged_artists |
| `fff e621 verify file-paths` | Verify posts.file_path and repair broken paths |
| `fff e621 verify file-names` | Make posts.file_name agree with the files on disk |
| `fff e621 match-downloads` | Build dl_fav/dl_nsfw/dl_sfw tables and match downloaded files to posts |
| `fff e621 ai process` | All AI post processing (AI-deleted cleanup, AI tags, flagged AI artists); also part of 'fff e621 sync' |
| `fff e621 ai remove-tagged` | Remove posts with AI tags (move file, unfavorite, downvote, delete from DB) |
| `fff e621 ai remove-artists` | Remove posts by artists listed in ai_flagged_artists |
| `fff e621 favorites add-missing` | Favorite e621 posts the sorter knows about but that aren't favorited, then sync |
| `fff e621 favorites swap-duplicates` | Move favorites from 'inferior/duplicate of' posts to their replacements |
| `fff e621 favorites from-md5` | Look up md5s from data/md5_search/md5.txt and favorite the matches |
| `fff e621 files find-deleted` | Look for local files of deleted posts (checks up to 10 posts) |
| `fff e621 files unfavorited` | Interactive menu for unfavorited posts (archive / delete) |
| `fff e621 files restore` | Restore files listed in a deleted-files CSV (one-off tool) |
| `fff e621 report favorites-diff` | Compare database post ids with live favorites |
| `fff e621 report relationships` | Print parent/child relationship statistics |
| `fff e621 db-export` | Download e621 DB exports and load them into e621_db_dump_* tables |
| `fff e621 reset-db` | Drop all e621_posts_db tables (asks to confirm) |
| `fff db inspect-hashes` | Print the image_sorting_hashes schema and hash columns |
| `fff fluffle search` | Reverse-search images with reverse search (+FuzzySearch), process e621/FA matches, sort the rest |
| `fff fluffle process-e621` | Process stored exact e621 matches: favorite, download, move existing code (applies the blacklist) |
| `fff fluffle process-fa` | Process stored exact FA matches (no e621 match): favorite, download, move existing code |
| `fff fluffle sync-fa-favorites` | Favorite on FA the images that matched both e621 and FA |
| `fff fluffle verify-db` | Print recent rows and counts from image_search |
| `fff fa rename-authors` | Rename *-FA-* files from author name to author id (mappings from the search database; interactive) |
| `fff fa rename-authors-manual` | Same, with mappings from config/fa_author_mappings.yaml (interactive) |
| `fff sort` | Sort and archive files, organize tags and artists, maintain the library, and find duplicates |
| `fff e621 fix-no-source-names` | Rename files whose path name differs from posts.file_name (excludes Fav/Artists, Tag Sorted, ...) |
| `fff dupes advanced` | Multi-stage duplicate finder (currently broken) |
| `fff db report space` | Sum file_size per image_sorting_hashes table |
| `fff db report archive-tables` | List the archive_* tables |
| `fff db diagnose all-files-view` | Diagnose the all_files view (artist-scan filters) |
| `fff db diagnose op-logging` | Exercise file_operations logging (writes and deletes test rows) |
| `fff hash` | Hash files / show info / find duplicates (v1 media_hasher CLI) |
| `fff gui manager` | Unified manager for workflows, settings, and desktop tools |
| `fff gui duplicates` | Duplicate manager GUI |
| `fff gui tag-sort-config` | Tag sorting rule editor GUI |
| `fff gui artist-config` | Artist config editor GUI (sort_artists.yaml) |
| `fff fa sync-external` | Track FA source links from e621 posts and favorite them on FA |
| `fff gui blacklist-review` | Review blacklisted posts (auto-deletes critical/AI-tagged posts first) |
| `fff gui artist-review` | Review posts by artists from review_artists.yaml |
| `fff web` | Local web gallery for the archive (Flask) |

## `fff sort` Sub-commands

Sorting, archiving, indexing, and library maintenance commands.

| Command | What it does |
|---|---|
| `fff sort adupe` |  |
| `fff sort advanced-duplicates` | Find duplicates using multi-stage pipeline (fast and efficient) |
| `fff sort archive` | Archive old files |
| `fff sort artist-config` | Configure favorite artists (GUI) |
| `fff sort artist-sort` | Sort files by favorite artists |
| `fff sort cleanup` | Delete empty folders |
| `fff sort duplicates` | Manage duplicate files |
| `fff sort maintenance` | Run maintenance tasks |
| `fff sort pool-sort` | Sort files by e621 pool membership |
| `fff sort post-id` | Manage files by e621 post IDs |
| `fff sort setup` | Initial setup and configuration |
| `fff sort sort` | Sort and organize files |
| `fff sort status` | Show system status |
| `fff sort tag-sort` | Sort files by e621 tags |
| `fff sort tag-sort-config` | Configure tag sorting rules (GUI) |
| `fff sort unarchive` | Restore recent files from archives |
| `fff sort workflow` | Full workflow: deleted posts, tag sort, sort+hash, artists, archive, maintenance, cleanup (was run.py) |
| `fff sort fa-backlog` | Move -FA- files into their 'Furaffinity - <Rating>' folders (was fa_backlog_sort.py) |
