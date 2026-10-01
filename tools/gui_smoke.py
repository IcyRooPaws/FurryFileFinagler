"""Smoke-test every GUI in the test environment.

    python tools/gui_smoke.py [--shots DIR] [name ...]

Each GUI runs in its own process with FFF_ENV=test and all HTTP stubbed (no e621/FA calls):
it is built with real test data, processes events for a few seconds (its widget tree is
counted; --shots also saves a screenshot, window brought to the front first) and is
closed. Any Tk callback exception or error fails the check. The config editors work on
temporary copies of the YAML files and must leave them byte-for-byte unchanged. The Flask
archive browser is checked through its test client (no server, no window).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

FFF = Path(__file__).resolve().parents[1]
CHECKS = ["tag-sort-config", "artist-config", "duplicates", "blacklist-review", "artist-review-ui",
          "post-id-review", "archive-browser"]


# --- runs inside the child process -----------------------------------------------

def _walk(widget):
    yield widget
    for child_widget in widget.winfo_children():
        yield from _walk(child_widget)


def _sample_posts(limit=5):
    from fff.archive.config import load_config
    from fff.archive.db import connect_to_mysql

    conn = connect_to_mysql(load_config()["mysql"])
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, file_name, file_path, rating FROM posts WHERE file_path IS NOT NULL "
                "AND file_type <> 'video' ORDER BY id DESC LIMIT 200")
    rows = [r for r in cur.fetchall() if r["file_path"] and os.path.exists(r["file_path"])][:limit]
    cur.close()
    for r in rows:
        r["blacklisted_tags"] = "smoke-test"
        r["matched_tags"] = "smoke-test"
    return conn, rows


def child(name, shot_dir):
    import tkinter as tk

    sys.path.insert(0, str(FFF / "tools" / "parity"))
    import stub_http  # noqa: E402

    stub_http.install(Path(tempfile.gettempdir()) / f"fff_gui_smoke_{name}_http.json")
    errors = []
    tk.Tk.report_callback_exception = lambda self, exc, val, tb: errors.append(
        "".join(traceback.format_exception(exc, val, tb)))
    result = {"name": name, "errors": errors}

    import logging

    class _Collect(logging.Handler):  # errors the GUIs only log (they catch their own exceptions)
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                errors.append(f"logged {record.levelname}: {record.getMessage()}")

    logging.getLogger().addHandler(_Collect())
    tmp = Path(tempfile.mkdtemp(prefix="fff_gui_smoke_"))

    if name == "archive-browser":
        from fff.gui import archive_browser

        client = archive_browser.app.test_client()
        statuses = {}
        for url in ("/", "/api/posts?page=1", "/api/posts?q=solo&rating=explicit&sort=created_at"):
            statuses[url] = client.get(url).status_code
        data = client.get("/api/posts?page=1").get_json() or {}
        posts = data.get("posts") or data.get("results") or []
        if posts:
            pid = posts[0].get("id")
            statuses[f"/post/{pid}"] = client.get(f"/post/{pid}").status_code
        _, with_files = _sample_posts(1)  # a post whose file exists on disk
        if with_files:
            pid = with_files[0]["id"]
            statuses[f"/media/{pid}"] = client.get(f"/media/{pid}").status_code
            if statuses[f"/media/{pid}"] != 200:
                errors.append(f"/media/{pid} returned {statuses[f'/media/{pid}']} for an existing file")
        result.update(statuses=statuses, posts_on_page=len(posts))
        if any(s >= 500 for s in statuses.values()):
            errors.append(f"HTTP 5xx: {statuses}")
        return result

    root = tk.Tk()
    watched = {}
    if name == "tag-sort-config":
        from fff.gui.tag_sort_config_ui_v2 import TagSortConfigApp
        from fff.sorter import paths as sorter_paths

        copy = tmp / "tag_sorting.yaml"
        shutil.copy2(sorter_paths.TAG_SORTING, copy)
        watched[copy] = hashlib.md5(copy.read_bytes()).hexdigest()
        app = TagSortConfigApp(root, copy)
    elif name == "artist-config":
        from fff.gui.artist_config_ui import ArtistConfigApp
        from fff.sorter import paths as sorter_paths

        copy = tmp / "sort_artists.yaml"
        shutil.copy2(sorter_paths.SORT_ARTISTS, copy)
        watched[copy] = hashlib.md5(copy.read_bytes()).hexdigest()
        app = ArtistConfigApp(root, copy)
    elif name == "duplicates":
        from fff.gui.duplicate_manager_ui import DuplicateManagerGUI

        app = DuplicateManagerGUI(root)
    elif name in ("blacklist-review", "artist-review-ui"):
        from fff.archive.config import load_config
        from fff.gui.blacklist_review_ui import BlacklistReviewUI

        conn, posts = _sample_posts()
        result["posts"] = len(posts)
        kwargs = {} if name == "blacklist-review" else {
            "window_title": "Artist Review UI", "context_label_text": "Artist:", "context_label_color": "blue"}
        app = BlacklistReviewUI(root, posts, conn, load_config()["e621"], kept_file=str(tmp / "kept.txt"), **kwargs)
    elif name == "post-id-review":
        from fff.archive.scripts.database.validate_post_ids_in_filenames import MismatchReviewUI

        _, posts = _sample_posts(2)
        groups = [{"file_path": p["file_path"], "filename": os.path.basename(p["file_path"]),
                   "filename_id": p["id"], "posts": [p["id"], p["id"] + 1]} for p in posts]
        app = MismatchReviewUI(root, groups)
    else:
        raise SystemExit(f"unknown check {name}")

    def shoot():
        try:
            root.update_idletasks()
            w, h = root.winfo_width(), root.winfo_height()
            result["window"] = [w, h, root.title()]
            result["widgets"] = sum(1 for _ in _walk(root))
            if not shot_dir or w <= 10 or h <= 10:
                return
            # A screen grab captures whatever is on top of that area, so bring the window to
            # the front first (otherwise it can capture other applications' windows).
            from PIL import ImageGrab

            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
            root.update()
            time.sleep(0.5)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(Path(shot_dir) / f"{name}.png")
        except Exception as e:  # screenshots are best effort
            result["screenshot_error"] = str(e)

    root.after(3500, shoot)
    root.after(4500, root.destroy)
    root.mainloop()
    _ = app
    for path, digest in watched.items():
        if hashlib.md5(path.read_bytes()).hexdigest() != digest:
            errors.append(f"{path.name} was modified by just opening the editor")
    if watched:
        result["config_copy_unchanged"] = not any("modified" in e for e in errors)
    return result


# --- orchestrator -----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", default=CHECKS)
    ap.add_argument("--shots", help="Also save a screenshot of each window (brought to the front first) here")
    ap.add_argument("--child", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        try:
            result = child(args.child, args.shots)
        except Exception:
            result = {"name": args.child, "errors": [traceback.format_exc()]}
        print("RESULT " + json.dumps(result, default=str))
        return 0

    if args.shots:
        Path(args.shots).mkdir(parents=True, exist_ok=True)
    env =dict(os.environ, FFF_ENV="test", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    failures = 0
    for name in args.names:
        start = time.time()
        shots = ["--shots", args.shots] if args.shots else []
        proc = subprocess.run([sys.executable, __file__, "--child", name] + shots,
                              cwd=FFF, env=env, capture_output=True, text=True, timeout=180)
        line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
        result = json.loads(line[7:]) if line else {"name": name, "errors": [proc.stderr[-2000:] or "no result"]}
        ok = not result["errors"] and proc.returncode == 0
        failures += not ok
        detail = {k: v for k, v in result.items() if k not in ("name", "errors")}
        print(f"{'OK  ' if ok else 'FAIL'} {name:<18} {time.time() - start:4.0f}s  {detail}")
        for e in result["errors"]:
            print("     " + e.strip().replace("\n", "\n     ")[:1500])
    if args.shots:
        print(f"\nScreenshots: {args.shots}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
