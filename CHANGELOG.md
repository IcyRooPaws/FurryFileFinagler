# Changelog

Every change to FurryFileFinagler, newest first. Each commit is one version, starting at 1.0.0
for the first commit. A commit that only updates this changelog for a release doesn't get a
version of its own. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Version numbers follow [Semantic Versioning](https://semver.org/):

- **Major** (2.0.0): a change that breaks an existing setup, such as a config or database
  format that needs migrating. None so far.
- **Minor** (1.x.0): something new you can do, or a change to how something works.
  Retiring one-off migration commands also counts as minor.
- **Patch** (1.x.y): fixes, internal clean-up, tests and documentation.

The version lives in `fff/__init__.py` (`fff --version` prints it). `pyproject.toml` reads it
from there, and a test checks that the newest entry here matches it.

## [1.30.0] - 2026-11-10

### Changed
- **The archive gallery loads much faster.**
  - The grid shows small cached previews instead of every post's original file. One page went
    from about 200 MB to under 1 MB.
  - Previews are made once and kept in `data/cache/thumbs`, and are made again only when a file
    changes. A page already seen loads in a few hundredths of a second; a first visit makes its
    previews in about 85 ms each.
  - Videos show a still frame with a play badge, instead of opening a video player per
    thumbnail.
  - Browsers reuse previews for 7 days and full files for 1 day, instead of re-checking every
    file on each visit. Shared caches such as proxies may not keep them.
  - Previews and files no longer need a database lookup each; the path comes from the post
    listing.

## [1.29.0] - 2026-11-10 (`3dfd89d`)

### Added
- **Guided first-time setup.** A new install must finish a step-by-step wizard before FFF can be
  used. `setup.bat`, `fff setup` and the FFF Manager all open it, and every other `fff` command
  stops with "FFF isn't set up yet" until it's done. The steps:
  1. Pick the optional features you'll use.
  2. Database: test the connection, then create the databases and every table.
  3. e621 account: checked by signing in.
  4. Folders: four root folders lay out the rest; missing folders are created with one click.
  5. Extras: the FuzzySearch key and FurAffinity cookie consent, only for the features you picked.
  6. Final checks.

  Progress is saved at every step, so setup can be resumed. Existing installs count as set up.
  `fff setup --wizard` runs it again.
- **Trends over time** page (`/trends`) in the archive browser. A month-by-month heatmap shows
  what share of each month's archived posts carried each artist, copyright, character, species
  or fetish tag, over the last 3, 6 or 12 months. The initial import is its own "Earlier"
  column, with a posts-per-month row on top, a "Show numbers" table view and hover details.
- A **Copyrights** list in the archive browser's trending sidebar.
- **Version numbers and this changelog.** Every commit since the first has a version, starting
  at 1.0.0. `fff --version` prints the current one.

### Changed
- The archive browser's pages (posts grid, post page, trends) use an e621-style layout:
  - Posts / Trends tabs, compact thumbnails with a rating-letter and ID bar, and the trending
    sidebar as color-coded tag lists.
  - The post page has e621's tag-section sidebar.
  - Colors come from the FFF artwork palette, with e621's tag-category colors lightened for
    contrast.
- `fff setup` opens the settings editor once setup is finished.
- The README leads with highlights and covers every feature. Its nightly-run section now lists
  the database backup and the AI-cleanup stage, which it used to leave out.
- The version is set only in `fff/__init__.py`, and `pyproject.toml` reads it from there
  (it said 0.1.0). Tests check that the changelog and the README match it.

### Fixed
- `manager.bat` didn't start on a fresh install: `fff gui manager` refused to run without a
  config.yaml. The Manager now opens and shows the setup.
- Creating tables from a background thread failed with "signal only works in main thread",
  because the sorter's hash manager installed a Ctrl+C handler at import time. It now does
  that only on the main thread.
- The settings editor's "Save and run checks" crashed when the database checks were included.
  It called a check that no longer existed.
- Archive browser thumbnails whose file can't be read show a "No file" box instead of broken
  alt text.

## [1.28.1] - 2026-11-10 (`30a87c8`)

### Fixed
- Clicking a tag or artist in the archive browser matched any name containing it, so `macro`
  also returned every `macropod` post. Trending names, search suggestions and a post's tags now
  search for the exact tag or artist. Typed searches still match parts of names.

## [1.28.0] - 2026-11-10 (`1799e05`)

### Added
- **Default fetish list.** Tags named in `data/kink_tags.txt` count as fetish until you mark them
  otherwise in the Generic tags tool, where they show as "Fetish (list)".
- **Most posts / Rising** toggle for the trending lists. Most posts sorts by recent post count;
  Rising sorts by how much more often a name appears than usual.

### Fixed
- The Artists trending list showed e621 warning tags (`conditional_dnp`, `avoid_posting`,
  `sound_warning`, ...) as if they were artists.

## [1.27.0] - 2026-11-10 (`d82e463`)

### Added
- **Generic tags tool** (`fff gui generic-tags`, also in the Manager). Go through the archive's
  tags, most-used first, and mark each one generic, not generic or fetish (keys G, N, F; U
  clears, Ctrl+Z undoes). It also gives a copyable MySQL query that leaves the generic tags out.
  Marks are stored in a new `tag_classifications` table.
- **Trending sidebar** in the archive browser: top-10 Artists, Characters, Species and Fetish
  lists for the last 7, 30 or 90 days. Tags marked generic are left out.

### Fixed
- On a post page, Species was the last tag section, under Meta and a long General list. Sections
  now follow e621's order: Copyright, Character, Species, General, Lore, Meta.

## [1.26.0] - 2026-11-10 (`2877256`)

### Added
- "Open file" and "Show in folder" buttons in the archive browser, on thumbnails and post pages.
  They open the file in its default app or select it in Explorer. They are offered only to a
  browser on the same computer, and other websites can't trigger them.

## [1.25.0] - 2026-11-10 (`ec4c3ec`)

### Added
- Media type filter in the archive browser (images, GIFs, videos, other). Deleted posts whose
  stored type is `unknown` are classified by their file's extension.

## [1.24.2] - 2026-11-09 (`5593da2`)

### Fixed
- The rate-limit test failed about two runs in three on Python 3.12 for Windows, because the
  coarse `time.monotonic()` clock ticks only every ~15.6 ms there. The limiter now uses
  `time.perf_counter()`.

## [1.24.1] - 2026-11-09 (`aecbbb2`)

### Fixed
- Incoming files are checked for duplicates against the whole library, not one folder. The old
  archive check compared a combined hash with single-hash columns and never matched, so about
  260 archived files came back into the library. Favorites, manual sort and other folders were
  never checked, and tag sort checked nothing. Duplicates now go to the duplicates folder and
  the existing copy is kept.

## [1.24.0] - 2026-11-09 (`f2d0e7c`)

### Added
- `fff db backup` and `fff db restore`.
  - Backups are consistent snapshots of all three databases, saved as gzipped SQL with a
    manifest, keeping the newest few.
  - Restore goes to the test databases by default; `--prod` needs a typed confirmation and
    backs up production first.
  - `fff daily` runs a backup first, and `fff doctor` warns when the newest backup is too old.

### Fixed
- FLOAT values (source-search match distances) are read at full precision, so they survive a
  backup and restore unchanged.

## [1.23.0] - 2026-11-09 (`93f97ad`)

### Added
- `fff db init` creates the three databases and every table FFF uses.
- `fff db snapshot` writes the live schema to `sql/schema/`.
- `fff doctor` reports missing tables.

### Changed
- The `all_files` view is generated from the hash tables that exist, instead of a hand-kept SQL
  file that had drifted.

### Fixed
- Files in manual-sort GIFs and possible-missing-files were invisible to everything that
  searches `all_files`.

## [1.22.0] - 2026-11-09 (`6d8124f`)

### Changed
- Every web request goes through one shared client with per-service rate limits, timeouts and
  capped retries that honour Retry-After. It is configured in config.yaml's new `http:` block.
  A POST is never repeated after a server error, because a repeated e621 vote undoes itself.

### Fixed
- The favorites fetch retried a timeout forever. It now gives up and fails the sync, instead of
  hanging or continuing with a partial list.
- About 25 e621 requests had no rate limit.
- Three backfills failed every post with a TypeError.

## [1.21.2] - 2026-11-09 (`f3ba978`)

### Added
- CI (ruff on Linux; tests on Windows with Python 3.12 and 3.14) and a pinned `requirements.txt`.

### Fixed
- Three error handlers raised NameError, so the real error was never shown: duplicate manager
  scan, similarity search, and tag rules "apply post dates".
- Ctrl+C now stops a run; 17 bare `except:` clauses used to swallow it.
- Plus hundreds of lint clean-ups.

## [1.21.1] - 2026-11-09 (`7520f36`)

### Fixed
- `--help` on forwarded commands (e.g. `fff e621 sync --help`) works without a config.yaml.

## [1.21.0] - 2026-11-09 (`5f71575`)

### Added
- `fff e621 files orphaned` moves files left behind by posts that are no longer favorites. It
  confirms each post with e621 first, and is a dry run unless `--execute`.

### Changed
- Removing a post (unfavorite during sync, Blacklist review delete, the AI cleanups) moves every
  local copy of its files, not just the stale `posts.file_path`. 129 files had been left behind
  in production.
- Unfavorited posts' files go to the unfavorited folder instead of the recycle bin.

### Fixed
- `check_if_favorited` read every post as "not favorited".
- Tools opened inside the Manager wrote empty log files.

## [1.20.2] - 2026-11-09 (`c645966`)

### Changed
- Every MySQL connection goes through one module, so the test-environment redirect always
  applies.

### Fixed
- Two archive helpers built a whole new connection pool on every call.
- Several connections ignored the configured port.

## [1.20.1] - 2026-11-09 (`eb01c96`)

### Added
- `docs/IMPROVEMENT_PLAN.md`: a 21-part plan for code health and new features.

## [1.20.0] - 2026-11-09 (`c0de038`)

### Added
- Blacklist review can remove a tag from the blacklist, move it to the critical blacklist, or
  ignore it via the new `blacklist_ignore.txt`.
- A tool that exports downloaded FA posts to the Auto-Downloader's JSON file.
- Dark mode for the desktop windows.

## [1.19.3] - 2026-11-03 (`844dadb`)

### Fixed
- Stopping a command in the Manager left the real process running in the background, where it
  overlapped the next run. Stop and close now end the whole process tree.
- `fff daily` and `fff fluffle search` refuse to start while another copy is running.
- When a command's console went away, every match failed with `[Errno 22]`. Output now keeps
  going to the log file.
- An FA match that is already the named FA download is moved instead of downloaded again.

## [1.19.2] - 2026-10-26 (`50b376d`)

### Fixed
- `fff fluffle search`, `process-e621`, `process-fa` and `fff fa sync-external` log to
  `logs/fluffle/`. Before this, their output went only to a console the daily task discards.

## [1.19.1] - 2026-10-25 (`9600473`)

### Fixed
- Stale duplicate hash rows made file moves fail on the unique-path key.
- Posts re-credited to a favorite artist after download were moved out of the artist folder and
  back again on every nightly run.

## [1.19.0] - 2026-10-25 (`f69d511`, merged as PR #1 in `fe0b317`)

### Added
- `fff consent browser-cookies` (`--status`, `--revoke`) for scheduled runs. FFF asks once
  before reading browser cookies, and saves the answer in `data/consent.json`.

### Changed
- FurAffinity cookies are read from Waterfox, Firefox, LibreWolf, Floorp or Zen (whichever
  visited FurAffinity last), and sent with that browser's User-Agent.

### Fixed
- FA favoriting failed because Cloudflare challenged every request.
- A blocked FA status check is reported as unknown instead of "Not favorited".

## [1.18.0] - 2026-10-22 (`eee431f`)

### Added
- Posts e621 deleted as AI are cleaned up automatically: archived, the artist flagged,
  unfavorited, the file trashed and the row removed. This runs as `fff e621 ai process`, its
  own `fff daily` stage.
- A post's FA source is unfavorited when the post leaves your e621 favorites. Failed attempts
  are queued and retried.

### Changed
- The AI artist list lives only in the `ai_flagged_artists` table.
- `fa sync-external` retries failed FA favorites, and treats FA errors as unknown.

## [1.17.1] - 2026-10-08 (`77fca4a`)

### Fixed
- Artist names with a leading `.` (e.g. `.gearsy`) produced file names starting with a dot.
  The dot is now stripped.

## [1.17.0] - 2026-10-07 (`01f5938`)

### Added
- Search suggestions in the archive browser: artists and tags as you type.

### Fixed
- Tag sorting sent safe-rated files to the NSFW folders. Files rated safe now go to the SFW
  pictures, videos and GIFs folders.

## [1.16.0] - 2026-10-07 (`ef95efc`)

### Added
- Video visual match in the Duplicates tool. It finds the same video at different quality,
  letterboxed, or trimmed, by comparing frames sampled across each video.

### Fixed
- Damaged webms no longer flood the console with FFmpeg errors.

## [1.15.0] - 2026-10-01 (`76539cb`)

### Removed
- One-off legacy migration scripts and their commands.

### Added
- `docs/SQLITE_BACKEND.md`: a plan for an SQLite backend.

## [1.14.0] - 2026-10-01 (`a7e5e1e`)

### Added
- Desktop tools open inside the Manager, and still run on their own.
- One shared theme for every FFF window.

## [1.13.0] - 2026-10-01 (`23f75ac`)

### Added
- The FFF Manager (`fff gui manager`, `manager.bat`): one window for workflows, settings and
  the desktop tools.

## [1.12.0] - 2026-10-01 (`fd27d8a`)

### Changed
- The setup window has a tabbed layout, folder pickers and a "Show credential values" toggle.

## [1.11.0] - 2026-10-01 (`4ed49ab`)

### Added
- `setup.bat`: creates the virtual environment, installs everything, then opens setup.

## [1.10.0] - 2026-10-01 (`e6c9b44`)

### Changed
- FFF is self-contained: it no longer refers to the separate projects it was built from.
- `fff setup` opens a setup window that starts from the example config files.

### Removed
- Migration code and docs for the old projects.

## [1.9.1] - 2026-10-01 (`4760ceb`)

### Fixed
- Posts whose artist names contain characters Windows rejects (`< > : " / \ | ? *`) failed to
  download on every run. Those characters become `_`.

## [1.9.0] - 2026-10-01 (`4f525d6`)

### Changed
- `fff daily` runs the same order as the scheduled "Sort Pictures" task: fluffle search, sort,
  e621 sync, sort, FA sync. It continues past failed stages.

### Added
- `fff_daily.ps1`, a drop-in replacement for the scheduled task.

## [1.8.0] - 2026-10-01 (`d8391ae`)

### Added
- `fff daily`: runs each stage as its own process, with skip, stop-on-error and dry-run
  options, and per-stage summaries in `logs/daily/`.

## [1.7.2] - 2026-10-01 (`51c68c6`)

### Added
- The media sorter's tests, `--help` coverage for every sort and hash command, `docs/USAGE.md`,
  and a generated `docs/COMMANDS.md`.

## [1.7.1] - 2026-10-01 (`6f93c8a`)

### Added
- `tools/gui_smoke.py`: opens every GUI in the test environment and fails on errors.

## [1.7.0] - 2026-10-01 (`66ea26c`)

### Added
- Media sorting: `fff sort` (all 14 sub-commands), `fff sort workflow`, `fff hash`, and the
  sorter GUIs under `fff gui`. Verified against the original on a 412-file sample library.

### Fixed
- The duplicates GUI import, logging calls, the maintenance verify result, and hard-coded
  database credentials in a diagnostic.

## [1.6.0] - 2026-09-30 (`f579c6a`)

### Added
- Reverse-image search: `fff fluffle search`, `process-e621`, `process-fa`,
  `sync-fa-favorites`, `verify-db`, and `fff fa rename-authors`.
  - Every file move is logged.
  - The tag blacklist is applied.
  - Verified against the original on 60 images.

### Fixed
- Reverse search's JSON responses landed in the e621 responses folder.

## [1.5.1] - 2026-09-30 (`5ea7f05`)

### Changed
- Blacklist review's manual Delete sends files to the recycle bin.
- Kept-post lists moved to `data/`.

### Fixed
- `fff e621 db-export` downloads failed.

## [1.5.0] - 2026-09-30 (`37baa87`)

### Added
- `fff db rewrite-test-paths`, plus tools to copy and verify the production folders for the
  test environment.

## [1.4.0] - 2026-09-30 (`fd6ccde`)

### Added
- `fff db copy-data`: copies production data into the test databases and verifies row counts.

## [1.3.0] - 2026-09-30 (`93f718a`)

### Added
- The e621 archive: favorites sync, backfills, verification, favorites management, file
  management, maintenance tools and the review GUIs.

## [1.2.0] - 2026-09-30 (`c9e98c5`)

### Added
- A prod/test environment switch (`environment:`, `FFF_ENV`, `fff --env test`) that points every
  module at `test_*` databases.
- `fff db copy-schema`.

## [1.1.0] - 2026-09-30 (`81d0b9c`)

### Added
- Shared core: file naming, logged file operations, timestamps, image hashing, and the e621,
  FurAffinity, reverse-search and FuzzySearch clients.

## [1.0.0] - 2026-09-30 (`f0ca87d`)

### Added
- The `fff` package and command line.
- Unified `config.yaml` plus `secrets.yaml`.
- `fff setup`, `fff config show` and `make-example`.
- `fff doctor`: checks packages, folders, databases and the `all_files` view.
- Shared database connections and logging.
- Only example config files are committed.
