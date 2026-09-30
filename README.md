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

## Test vs production databases

`environment: prod | test` in `config/config.yaml` picks the databases for every module. In `test`,
each database name gets a `test_` prefix (`test_e621_posts_db`, `test_image_sorting_hashes`,
`test_image_search`). Override per run with `FFF_ENV=test` or `fff --env test <command>`
(precedence: `--env` > `FFF_ENV` > config). Commands print a banner on stderr in test mode.
Create the test databases with `fff db copy-schema`.

Tests: `.venv\Scripts\python -m pytest`
