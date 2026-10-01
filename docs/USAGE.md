# Using FurryFileFinagler

`fff` combines three earlier projects into one command:

- **e621 archive**: mirrors your e621 favorites into `e621_posts_db` and keeps your local files matched to them.
- **reverse-image search**: finds the source of images in your inbox folders and downloads the existing code.
- **media sorter**: sorts, hashes, archives and de-duplicates the library in `image_sorting_hashes`.

The full command list is in [COMMANDS.md](COMMANDS.md). [project notes](../project notes) records every decision and every change made during the merge.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
fff setup      # build config/config.yaml + secrets.yaml from the current features
fff doctor              # packages, configured folders, all three databases
```

### Without the current features

Copy each `config/*.example.yaml` and `config/lists/*.example.txt` to the same name without `.example`, then fill them in.

Only the examples are committed. If you change the layout of `config.yaml`, run `fff config make-example --force` to update the example.

## Where things live

| Path | Contents |
|---|---|
| `config/config.yaml` | All settings, with no secrets. It has sections `fluffle:`, `archive:` and `sorter:` (each in its previous component's layout), plus the shared `database:` and `e621:` |
| `config/secrets.yaml` | DB password, the e621 login and API key (one account, shared by every module), and the FuzzySearch key |
| `config/tag_sorting.yaml`, `sort_artists.yaml`, `review_artists.yaml`, `fa_author_mappings.yaml`, `lists/*.txt` | The current features' rule and list files |
| `data/` | AI-artist list, kept-post lists, md5 lists, caches (e621 pages and posts, reverse search responses) |
| `logs/<archive\|sorter\|db>/` | One log file per run |

## A typical day

The three pipelines feed each other:

```powershell
fff fluffle search      # find sources for inbox images; download e621/FA existing code
fff sort workflow       # tag sort, sort + hash, artist sort, archive, maintenance
fff e621 sync           # favorites -> e621_posts_db, download missing files, AI cleanup, FA sync
fff sort workflow       # sort what the e621 sync downloaded
fff fa sync-external    # final FA favorites pass
```

Or run the whole daily sequence with one command. It is the same order as the old
`fff_daily.ps1`:

```powershell
fff daily                  # fluffle search -> sort workflow -> e621 sync -> sort workflow -> fa sync-external
fff daily --dry-run        # show what would run
fff daily --skip-fluffle --skip-favorites --stop-on-error
fff --env test daily       # the same against the test databases and E:\Testing
.ff_daily.ps1 -scheduled # what Task Scheduler runs (exit code = failed stages)
```

`fff daily` runs each stage as its own `fff` process, passing through `--env` and `--config`. Like the old script, it keeps going after a failed stage. Unlike the old script, it exits non-zero if any stage failed. It writes a one-line-per-stage summary to `logs/daily/`. It adds no logic of its own.

Each command accepts its original flags. For example:

- `fff e621 sync --skip-favorites`
- `fff sort workflow --dry-run --skip-archive`
- `fff sort sort --dry-run`

## What can change things outside this folder

| Command | Effects |
|---|---|
| `fff e621 sync` | Writes `e621_posts_db`. Downloads into `download.missing_files_directory`. Renames files whose artist tags changed. Sends files of unfavorited posts to the recycle bin (`delete_unfavorited_files`). Unfavorites and downvotes AI-tagged posts on e621 and moves their files to `archive.ai_cleanup`. Favorites on FurAffinity. |
| `fff fluffle search` / `process-e621` / `process-fa` | Favorites on e621 and FurAffinity. Downloads files. Moves the existing code to the source-found, not-found, blacklisted and other-sourced folders. Every file operation is logged to `file_operations`. |
| `fff sort ...` | Moves files between the library folders and archives. Writes `image_sorting_hashes`. |
| `fff gui blacklist-review` | Auto-deletes posts with critical or AI tags (recycle bin) and unfavorites them on e621, before the window opens. |

## Test environment

`environment: test` in `config/config.yaml` switches everything to test data. You can also use `FFF_ENV=test`, or `fff --env test <command>` for a single command.

In test mode:

- every database is `test_<name>`;
- every configured folder moves under `test_paths_root`, so `P:\IPL\x` becomes `E:\Testing\P\IPL\x`;
- the project's `data\` becomes `data\test\`;
- commands print a `[fff] TEST environment` banner.

e621, FurAffinity, reverse search and FuzzySearch are real services with no test version, so in test mode they are still called for real.

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
| Unit and pinning tests | `.venv\Scripts\python -m pytest` |
| Integration tests (test databases only) | `$env:FFF_INTEGRATION=1; .venv\Scripts\python -m pytest -m integration` |
| GUIs open and load data | `python tools/gui_smoke.py` |
| Same results as the existing code | `archive_sync_parity.py`, `fluffle_search_parity.py`, `sorter_workflow_parity.py`. Each runs an original and its port on identical input and compares databases, files and HTTP calls. |

## How the code was ported

Each current file was copied verbatim by a script in ``, which then applies exact, count-checked edits marked `# fff:`. Never edit a ported file by hand. Change its port script and re-run it.

- `python audit.py` shows how much each ported file differs from its original.
- [PORTING.md](PORTING.md) has the conventions.
- [existing code/](existing code/README.md) has the three projects' own docs.
