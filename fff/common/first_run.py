"""First-time setup: progress, the checks each step must pass, and folder helpers.

The guided wizard (fff.gui.first_run_ui) walks a new user through the database, their e621
account, folders and optional services. Until it is finished, FFF refuses to run anything
else: the CLI exits with ``NOT_SET_UP`` and the Manager shows the wizard instead of its pages.

Progress lives in ``data/setup_state.json``. An install that has a config.yaml but no state
file predates the wizard and counts as set up, so existing installs are never locked out;
running the wizard again on a finished install never un-finishes it.

Nothing here touches Tk, so the steps' checks can be tested and reused from the CLI.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fff.common import paths

STATE_FILE = paths.DATA_DIR / "setup_state.json"

NOT_SET_UP = ("FFF isn't set up yet. Finish the guided setup first: run 'fff setup' "
              "(or setup.bat, or open the FFF Manager).")

# Optional features chosen on the wizard's first page; they decide which extra steps apply.
FEATURES = {
    "sorting": ("Sort my library",
                "Hash and sort downloaded files into your folders, find duplicates, archive old files."),
    "reverse_search": ("Find sources for loose images",
                       "Reverse-search images without a source (Fluffle and FuzzySearch). "
                       "Needs a FuzzySearch API key."),
    "furaffinity": ("Use FurAffinity",
                    "Favorite and download matches on FurAffinity. Signs in with your browser's "
                    "FurAffinity cookies, which FFF asks permission for."),
}
DEFAULT_FEATURES = {"sorting": True, "reverse_search": False, "furaffinity": False}

# Values in the example files that mean "not filled in yet".
PLACEHOLDERS = {"change_me", "your_mysql_user", "your_e621_username"}
PLACEHOLDER_USER_AGENT = "yourappname"

# The example config keeps every folder under these four roots (fff.common.config_examples),
# so the wizard asks for four folders instead of ~60.
ROOTS = (
    ("D:/Media", "Media library", "Your sorted pictures, videos, GIFs and favorites."),
    ("D:/Work", "Work area", "Holding folders FFF moves files through: duplicates, review, unfavorited."),
    ("D:/Archive", "Long-term archive", "Where the sorter moves files older than a year."),
    ("C:/Users/you", "Downloads", "Your user folder; FFF looks in Downloads for new FurAffinity images."),
)


# --------------------------------------------------------------------------- progress

def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_complete(config_path: str | Path | None = None) -> bool:
    """True once setup is finished (or for installs set up before the wizard existed)."""
    state = load_state()
    if "completed" in state:
        return bool(state["completed"])
    return Path(config_path or paths.CONFIG_DIR / "config.yaml").exists()


def begin(config_path: str | Path | None = None) -> dict:
    """Start (or resume) the wizard. A finished install stays finished while it is re-run."""
    state = load_state()
    if "completed" not in state:
        state["completed"] = is_complete(config_path)
        state["started"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)
    return state


def record(state: dict, done_step: str, **values) -> dict:
    """Mark ``done_step`` done, remember ``values`` (e.g. features, the step to resume at), save."""
    state["done"] = sorted(set(state.get("done", [])) | {done_step})
    state.update(values)
    save_state(state)
    return state


def finish(state: dict) -> dict:
    state["completed"] = True
    state["finished"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)
    return state


# --------------------------------------------------------------------------- values

def is_placeholder(value) -> bool:
    """Empty, an example value, or built from one (e.g. a user agent naming 'CHANGE_ME')."""
    if value is None:
        return True
    text = str(value).strip().lower()
    return not text or PLACEHOLDER_USER_AGENT in text or any(token in text for token in PLACEHOLDERS)


def default_user_agent(login: str) -> str:
    return f"FurryFileFinagler/1.0 (by {login or 'your_e621_username'} on e621)"


def unfinished_values(config: dict, secrets: dict, features: dict) -> list[str]:
    """Required settings still empty or left at their example value."""
    def get(doc, *keys):
        for key in keys:
            doc = doc.get(key) if isinstance(doc, dict) else None
        return doc

    required = [
        ("Database user", get(config, "database", "user")),
        ("e621 username", get(secrets, "e621", "login")),
        ("e621 API key", get(secrets, "e621", "api_key")),
        ("e621 user agent", get(config, "e621", "user_agent")),
        ("e621 favorites username", get(config, "archive", "e621", "favorites_username")),
    ]
    if features.get("reverse_search"):
        required.append(("FuzzySearch API key", get(secrets, "fuzzysearch", "api_key")))
    problems = [f"{label} is not filled in" for label, value in required if is_placeholder(value)]
    password = get(secrets, "database", "password")
    if password is not None and str(password).strip().lower() == "change_me":
        problems.append("Database password is still CHANGE_ME")
    return problems


# --------------------------------------------------------------------------- database

def check_server(cfg: dict) -> tuple[bool, str]:
    """Can we sign in to the MySQL server with these settings?"""
    from fff.common import db

    try:
        conn = db.connect_server(cfg, connection_timeout=10)
    except Exception as exc:  # mysql.connector raises many types; show the message either way
        return False, f"Could not connect: {exc}"
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        cursor.close()
    finally:
        conn.close()
    return True, f"Connected to MySQL {version}."


def prepare_databases(config_path: Path, secrets_path: Path) -> tuple[dict, list[str]]:
    """Create the three databases and every table ('fff db init'), from the saved files.

    Returns ({module: [tables created]}, remaining schema problems).
    """
    from fff.clients import http
    from fff.common import config as fff_config
    from fff.common import schema

    fff_config.load(config_path, secrets_path, reload=True)
    http.reset()
    created = schema.init(progress=lambda _line: None)
    return created, schema.check()


# --------------------------------------------------------------------------- e621

def check_e621(login: str, api_key: str, user_agent: str) -> tuple[bool, str]:
    """Sign in to e621 with the username and API key (one request)."""
    import requests

    from fff.clients import http

    if is_placeholder(login) or is_placeholder(api_key):
        return False, "Enter your e621 username and API key."
    if is_placeholder(user_agent):
        return False, "Enter a user agent (e621 requires one that names you)."
    try:
        response = http.get("e621", f"https://e621.net/users/{quote(login.strip())}.json",
                            auth=(login.strip(), api_key.strip()), headers={"User-Agent": user_agent})
    except requests.RequestException as exc:
        return False, f"Could not reach e621: {exc}"
    if response.status_code in (401, 403):
        return False, "e621 rejected the username or API key. Check both and try again."
    if response.status_code == 404:
        return False, f"e621 has no user named {login.strip()!r}."
    if not response.ok:
        return False, f"e621 answered HTTP {response.status_code}. Try again in a moment."
    try:
        name = response.json().get("name") or login
    except ValueError:
        name = login
    return True, f"Signed in to e621 as {name}."


# --------------------------------------------------------------------------- folders

def _norm(path: str) -> str:
    return str(path).replace("\\", "/").rstrip("/")


def _under(path: str, root: str) -> str | None:
    """The part of ``path`` after ``root`` ('' for root itself), or None if it is elsewhere."""
    p, r = _norm(path), _norm(root)
    if p.lower() == r.lower():
        return ""
    if p.lower().startswith(r.lower() + "/"):
        return p[len(r):]
    return None


def map_strings(obj, fn):
    """A copy of ``obj`` with ``fn`` applied to every string (each line of a multi-line one)."""
    if isinstance(obj, dict):
        return {key: map_strings(value, fn) for key, value in obj.items()}
    if isinstance(obj, list):
        return [map_strings(value, fn) for value in obj]
    if isinstance(obj, str):
        if "\n" in obj:
            return "".join(fn(line[:-1]) + "\n" if line.endswith("\n") else fn(line)
                           for line in obj.splitlines(keepends=True))
        return fn(obj)
    return obj


def move_root(config: dict, old_root: str, new_root: str) -> dict:
    """Every folder under ``old_root`` moved to the same place under ``new_root``."""
    new_root = _norm(new_root)

    def move(value: str) -> str:
        rest = _under(value, old_root)
        return value if rest is None else new_root + rest

    return map_strings(config, move)


def replace_folder(config: dict, old: str, new: str) -> dict:
    """Every setting that uses folder ``old`` pointed at ``new`` instead."""
    new = _norm(new)
    return map_strings(config, lambda value: new if _norm(value).lower() == _norm(old).lower() else value)


def folders(cfg: dict) -> list[dict]:
    """The folders FFF is configured to use, once each: {path, settings, exists}."""
    from fff.cli import doctor

    by_path: dict[str, dict] = {}
    for label, path in doctor.configured_directories(cfg):
        entry = by_path.setdefault(_norm(path).lower(), {"path": _norm(path), "settings": []})
        if label not in entry["settings"]:
            entry["settings"].append(label)
    rows = sorted(by_path.values(), key=lambda row: row["path"].lower())
    for row in rows:
        row["exists"] = Path(row["path"]).is_dir()
    return rows


def roots_in_use(cfg: dict) -> list[tuple[str, str, str]]:
    """The example roots that still hold at least one configured folder."""
    paths_used = [row["path"] for row in folders(cfg)]
    return [root for root in ROOTS if any(_under(path, root[0]) is not None for path in paths_used)]


def create_folders(folder_paths) -> list[str]:
    """Create the folders; returns one message per folder that could not be created."""
    errors = []
    for folder in folder_paths:
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(f"{folder}: {exc.strerror or exc}")
    return errors


# --------------------------------------------------------------------------- final checks

def final_checks(config: dict, secrets: dict, features: dict) -> list[tuple[str, bool, str]]:
    """Every check the finished setup must pass: (title, ok, detail). Uses the saved files."""
    from fff.cli import doctor
    from fff.common import config as fff_config

    cfg = fff_config.deep_merge(config, secrets)
    results = []

    missing_modules = [name for name, status in doctor.check_modules() if status.startswith(doctor.MISSING)]
    results.append(("Python packages", not missing_modules,
                    "All installed." if not missing_modules
                    else "Missing: " + ", ".join(missing_modules) + ". Run setup.bat again."))

    unfinished = unfinished_values(config, secrets, features)
    results.append(("Settings filled in", not unfinished, "; ".join(unfinished) or "Nothing left at an example value."))

    databases = doctor.check_databases(cfg)
    failed = [f"{name}: {status}" for _module, name, status in databases if not status.startswith(doctor.OK)]
    results.append(("Databases", not failed, "; ".join(failed) or ", ".join(name for _m, name, _s in databases)))

    if not failed:
        schema_problems = [line for line in doctor.check_schema() if not line.startswith("run ")]
        results.append(("Tables", not schema_problems, "; ".join(schema_problems) or "Every table FFF uses exists."))

    missing = [row["path"] for row in folders(cfg) if not row["exists"]]
    results.append(("Folders", not missing, f"{len(missing)} missing, e.g. {missing[0]}" if missing
                    else f"All {len(folders(cfg))} exist."))

    e621 = secrets.get("e621") or {}
    ok, message = check_e621(e621.get("login") or "", e621.get("api_key") or "",
                             (config.get("e621") or {}).get("user_agent") or "")
    results.append(("e621 account", ok, message))
    return results


def short_path(path: str, width: int = 70) -> str:
    """``path`` shortened in the middle for narrow labels."""
    path = _norm(path)
    return path if len(path) <= width else path[: width // 2 - 1] + "…" + path[-(width // 2):]


def clean_label(setting: str) -> str:
    """'archive.download.images.safe' -> 'archive › download › images › safe'."""
    return re.sub(r"\.", " › ", setting)
