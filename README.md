# FurryFileFinagler (`fff`)

One toolkit combining **reverse-image search**, **e621 archive** and **media sorter**.
The current features are left untouched; see [project notes](project notes) for the full merge plan,
the command map and progress.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
fff setup      # builds config/config.yaml + secrets.yaml from the three current features
fff doctor              # checks packages, folders and all three databases
```

Without the current features, copy each `config/*.example.yaml` and `config/lists/*.example.txt`
to the same name without `.example`, and fill them in. The real files are gitignored; only
the examples are committed. After changing the layout of `config.yaml`, refresh the example
with `fff config make-example --force` (it replaces paths and personal values with placeholders).

| Real file (gitignored) | Example (committed) |
|---|---|
| `config/config.yaml` | `config/config.example.yaml` |
| `config/secrets.yaml` | `config/secrets.example.yaml` |
| `config/tag_sorting.yaml` | `config/tag_sorting.example.yaml` |
| `config/sort_artists.yaml` | `config/sort_artists.example.yaml` |
| `config/review_artists.yaml` | `config/review_artists.example.yaml` |
| `config/lists/blacklist.txt`, `critical_blacklist.txt`, `ai_tags.txt` | `config/lists/*.example.txt` |

## Commands available so far

| Command | What it does |
|---|---|
| `fff setup [--dry-run] [--force]` | Build the unified config from the existing code (read-only on them) |
| `fff config make-example [--force]` | Write a sanitized `config.example.yaml` from `config.yaml` |
| `fff config show [--module fluffle\|archive\|sorter]` | Print config, secrets redacted; `--module` shows what that module's ported code sees |
| `fff db copy-schema [--db ...] [--dry-run]` | Create/refresh empty `test_*` copies of the databases (structure only; re-running empties them) |
| `fff doctor [--skip-db] [--skip-deps]` | Packages, configured folders, DB connectivity, `all_files` view |

### e621 archive, FA, GUIs (implemented in e621 archive)

Commands marked in project notes §7. Scripts that had their own options take them unchanged (`fff e621 backfill pools --help`).

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
| `fff e621 ai cleanup-deleted` | Posts e621 deleted as AI: snapshot, unfavorite, optionally trash files, delete from DB (dry run by default) |
| `fff legacy add-deletion-reason-column` | One-off migration: add posts.deletion_reason |
| `fff legacy migrate-ai-archive-text` | One-off: import data/ai_generated_posts_archive.txt into the DB |
| `fff legacy migrate-ai-artists` | One-off: import data/ai_artist.md into ai_flagged_artists |
| `fff e621 verify file-paths` | Verify posts.file_path and repair broken paths |
| `fff e621 verify file-names` | Make posts.file_name agree with the files on disk |
| `fff e621 match-downloads` | Build dl_fav/dl_nsfw/dl_sfw tables and match downloaded files to posts |
| `fff e621 ai remove-tagged` | Remove posts with AI tags (move file, unfavorite, downvote, delete from DB) |
| `fff e621 ai remove-artists` | Remove posts by artists listed in data/ai_artist.md |
| `fff e621 favorites add-missing` | Favorite e621 posts the sorter knows about but that aren't favorited, then sync |
| `fff e621 favorites swap-duplicates` | Move favorites from 'inferior/duplicate of' posts to their replacements |
| `fff e621 favorites from-md5` | Look up md5s from data/md5_search/md5.txt and favorite the matches |
| `fff e621 files find-deleted` | Look for local files of deleted posts (checks 10 posts, as the original did) |
| `fff e621 files unfavorited` | Interactive menu for unfavorited posts (archive / delete) |
| `fff e621 files restore` | Restore files listed in a deleted-files CSV (one-off tool) |
| `fff e621 report favorites-diff` | Compare database post ids with live favorites |
| `fff e621 report relationships` | Print parent/child relationship statistics |
| `fff e621 db-export` | Download e621 DB exports and load them into e621_db_dump_* tables |
| `fff e621 reset-db` | Drop all e621_posts_db tables (asks to confirm) |
| `fff db inspect-hashes` | Print the image_sorting_hashes schema and hash columns |
| `fff fa sync-external` | Track FA source links from e621 posts and favorite them on FA |
| `fff gui blacklist-review` | Review blacklisted posts (auto-deletes critical/AI-tagged posts first) |
| `fff gui artist-review` | Review posts by artists from review_artists.yaml |
| `fff web` | Local web gallery for the archive (Flask) |

## Test vs production databases

`environment: prod | test` in `config/config.yaml` picks the databases for every module. In `test`,
each database name gets a `test_` prefix (`test_e621_posts_db`, `test_image_sorting_hashes`,
`test_image_search`). Override per run with `FFF_ENV=test` or `fff --env test <command>`
(precedence: `--env` > `FFF_ENV` > config). Commands print a banner on stderr in test mode.
Create the test databases with `fff db copy-schema`.

Tests: `.venv\Scripts\python -m pytest`
