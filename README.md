# FurryFileFinagler (`fff`)

`fff` is a toolkit for e621 favorites archiving, reverse-image source search,
and media-library sorting, hashing, archiving, and duplicate detection.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
fff setup               # guided configuration and first-run checks
fff doctor              # check packages, folders and all three databases

fff daily               # the daily run: fluffle search, sort, e621 sync, sort, FA sync
fff daily --dry-run     # what it would run
```

Use `fff --env test <command>` (or `environment: test` in `config/config.yaml`) to run
against the `test_*` databases and the `E:\Testing` copy of the library.

## Documentation

| Doc | Contents |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Setup, where things live, daily order, what each command touches, the test environment, checks |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Complete command reference (generated) |

## Config files

The real files are gitignored; only the examples are committed.

| Real file | Example |
|---|---|
| `config/config.yaml` | `config/config.example.yaml` (`fff config make-example --force` refreshes it) |
| `config/secrets.yaml` | `config/secrets.example.yaml` |
| `config/tag_sorting.yaml`, `sort_artists.yaml`, `review_artists.yaml`, `fa_author_mappings.yaml` | `config/*.example.yaml` |
| `config/lists/blacklist.txt`, `critical_blacklist.txt`, `ai_tags.txt` | `config/lists/*.example.txt` |

## Tests

```powershell
.venv\Scripts\python -m pytest                     # unit and CLI tests
$env:FFF_INTEGRATION=1; .venv\Scripts\python -m pytest -m integration   # test databases only
python tools/gui_smoke.py                          # every GUI opens and loads data
```
