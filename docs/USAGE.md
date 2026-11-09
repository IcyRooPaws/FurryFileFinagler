# Using FurryFileFinagler

`fff` provides one command-line interface for managing your archive and media library:

- **e621 archive**: mirrors favorites into `e621_posts_db` and matches posts to local files.
- **Source search**: finds image sources and processes matching downloads.
- **Library management**: sorts, hashes, archives and de-duplicates files in `image_sorting_hashes`.

The full command list is in [COMMANDS.md](COMMANDS.md).

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
fff setup               # guided configuration and first-run checks
fff db init             # create the three databases and every table FFF uses
fff doctor              # packages, configured folders, all three databases
```

The setup window starts from the committed examples unless real config files already exist. It keeps
credentials in `config/secrets.yaml`; database connectivity checks are opt-in and read-only.

### Manual setup

Copy each `config/*.example.yaml` and `config/lists/*.example.txt` to the same name without `.example`, then fill them in.

Only the examples are committed. If you change the layout of `config.yaml`, run `fff config make-example --force` to update the example.

## Where things live

| Path | Contents |
|---|---|
| `config/config.yaml` | All settings, with no secrets. It has `fluffle:`, `archive:` and `sorter:` sections, plus shared `database:` and `e621:` settings |
| `config/secrets.yaml` | DB password, the e621 login and API key (one account, shared by every module), and the FuzzySearch key |
| `config/*.yaml`, `config/lists/*.txt` | Sorting, review, and filtering rules |
| `data/` | AI-artist list, kept-post lists, md5 lists, and runtime caches |
| `logs/<archive\|sorter\|fluffle\|db>/` | One log file per run |

## A typical day

The search, archive, and sorting workflows feed into each other:

```powershell
fff fluffle search      # find sources for inbox images and process matches
fff sort workflow       # tag sort, sort + hash, artist sort, archive, maintenance
fff e621 sync           # favorites -> e621_posts_db, download missing files, AI cleanup, FA sync
fff sort workflow       # sort what the e621 sync downloaded
fff fa sync-external    # final FA favorites pass
```

Or run the full sequence with one command:

```powershell
fff daily
fff daily --dry-run
fff daily --skip-fluffle --skip-favorites --stop-on-error
fff --env test daily
```

`fff daily` runs each stage as its own `fff` process, passing through `--env` and `--config`. It continues after a failed stage unless `--stop-on-error` is set, exits non-zero if any stage failed, and writes a stage summary to `logs/daily/`.

Use each command's `--help` to see its options. For example:

- `fff e621 sync --skip-favorites`
- `fff sort workflow --dry-run --skip-archive`
- `fff sort sort --dry-run`

## What can change things outside this folder

| Command | Effects |
|---|---|
| `fff e621 sync` | Writes `e621_posts_db`. Downloads into `download.missing_files_directory`. Renames files whose artist tags changed. Moves every local copy of an unfavorited post's file to `archive.e621.unfavorited_directory` (`delete_unfavorited_files`). Unfavorites and downvotes AI-tagged posts on e621 and moves their files to `archive.ai_cleanup`. Favorites on FurAffinity. |
| `fff fluffle search` / `process-e621` / `process-fa` | Favorites on e621 and FurAffinity. Downloads files. Moves the existing code to the source-found, not-found, blacklisted and other-sourced folders. Every file operation is logged to `file_operations`. |
| `fff sort ...` | Moves files between the library folders and archives. Writes `image_sorting_hashes`. |
| `fff gui blacklist-review` | Auto-removes posts with critical or AI tags and unfavorites them on e621, before the window opens. Their files move to the unfavorited folder (critical tags) or `archive.ai_cleanup` (AI tags). |
| `fff e621 files orphaned --execute` | Moves library files of posts that are no longer in the database or your favorites to the unfavorited folder. |
| `fff db restore <backup> --prod` | Replaces the production databases with a backup, after you type a confirmation. Backs up the current state first. |

## Backups

`fff db backup` writes all three databases to `backup.directory` (default `data/backups`), one
folder per backup with a gzipped SQL file per database and a `manifest.json` of row counts and
checksums. It reads them inside one consistent snapshot, so a sync running at the same time can't
make the backup inconsistent, and it needs no `mysqldump`. The newest `backup.keep` backups are
kept. `fff daily` runs it first unless `backup.in_daily` is false or `--skip-backup` is given, and
`fff doctor` warns when the newest backup is older than `backup.max_age_days`.

```powershell
fff db backup                     # back up now
fff db backup --list              # list backups
fff db restore latest             # restore the newest backup into the test_* databases
fff db restore <folder> --prod    # restore into production (asks to confirm; backs up first)
```

A restore replaces every table, view and procedure in the backup, then checks each table's row
count against the manifest. It is slower than the backup (about as long as inserting every row).

## Test environment

`environment: test` in `config/config.yaml` switches everything to test data. You can also use `FFF_ENV=test`, or `fff --env test <command>` for a single command.

In test mode:

- every database is `test_<name>`;
- every configured folder moves under `test_paths_root`, so `P:\IPL\x` becomes `E:\Testing\P\IPL\x`;
- the project's `data\` becomes `data\test\`;
- commands print a `[fff] TEST environment` banner.

e621, FurAffinity, and FuzzySearch are real services with no test version, so in test mode they are still called for real.

### Setting up (or resetting) the test copy

```powershell
fff db copy-data                         # test_* databases = a copy of production (asks first)
fff db rewrite-test-paths                # stored file paths -> E:\Testing\...
python tools/copy_prod_to_test.py        # dry run: sizes + free space
python tools/copy_prod_to_test.py --execute
python tools/verify_test_copy.py         # every prod file has a copy
fff config test-dirs                     # where each configured folder goes
```

## Checks

| What | How |
|---|---|
| Unit and CLI tests | `.venv\Scripts\python -m pytest` |
| Integration tests (test databases only) | `$env:FFF_INTEGRATION=1; .venv\Scripts\python -m pytest -m integration` |
| GUIs open and load data | `python tools/gui_smoke.py` |
| Lint | `.venv\Scripts\python -m ruff check fff tests tools` |
