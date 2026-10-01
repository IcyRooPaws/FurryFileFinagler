"""``fff daily``: run the three pipelines in order (project notes §7).

1. ``fff e621 sync``       favorites -> e621_posts_db, downloads, AI cleanup, FA sync
2. ``fff fluffle search``  sources for inbox images; downloads into the sorter's inbox
3. ``fff sort workflow``   tag sort, sort + hash, artist sort, archive, maintenance

Each stage runs as its own ``fff`` process (so their logging setup, signal handlers and
sys.exit calls can't interfere with each other), with the same --env / --config. It adds
no logic of its own: running the three commands by hand does exactly the same.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime

from fff.common import paths

STAGES = [
    ("e621", ["e621", "sync"], "e621 favorites sync"),
    ("fluffle", ["fluffle", "search"], "reverse search reverse image search"),
    ("sort", ["sort", "workflow"], "media sorter workflow"),
]


def add_arguments(p) -> None:
    p.add_argument("--skip-e621", action="store_true", help="Don't run 'fff e621 sync'")
    p.add_argument("--skip-fluffle", action="store_true", help="Don't run 'fff fluffle search'")
    p.add_argument("--skip-sort", action="store_true", help="Don't run 'fff sort workflow'")
    p.add_argument("--skip-favorites", action="store_true",
                   help="Pass --skip-favorites to 'fff e621 sync' (no favorites fetch)")
    p.add_argument("--stop-on-error", action="store_true",
                   help="Stop after a stage fails (default: run the remaining stages anyway)")
    p.add_argument("--dry-run", action="store_true", help="Only print the commands that would run")


def plan(args) -> list[tuple[str, list[str], str]]:
    stages = []
    for key, argv, label in STAGES:
        if getattr(args, f"skip_{key}"):
            continue
        argv = list(argv)
        if key == "e621" and args.skip_favorites:
            argv.append("--skip-favorites")
        stages.append((key, argv, label))
    return stages


def _global_args(args) -> list[str]:
    out = []
    if getattr(args, "env", None):
        out += ["--env", args.env]
    if getattr(args, "config", None):
        out += ["--config", args.config]
    return out


def run(args, runner=subprocess.run) -> int:
    stages = plan(args)
    base = [sys.executable, "-m", "fff"] + _global_args(args)
    if not stages:
        print("Nothing to run (every stage skipped).")
        return 0
    if args.dry_run:
        for i, (_, argv, label) in enumerate(stages, 1):
            print(f"{i}. {label}: fff {' '.join(_global_args(args) + argv)}")
        return 0

    results = []
    started = datetime.now()
    for i, (key, argv, label) in enumerate(stages, 1):
        print(f"\n{'=' * 70}\n[fff daily] {i}/{len(stages)} {label}: fff {' '.join(argv)}\n{'=' * 70}", flush=True)
        t0 = time.time()
        try:
            code = runner(base + argv).returncode
        except KeyboardInterrupt:
            results.append((label, argv, 130, time.time() - t0))
            print("\n[fff daily] interrupted")
            break
        results.append((label, argv, code, time.time() - t0))
        if code != 0 and args.stop_on_error:
            print(f"[fff daily] {label} failed (exit {code}); stopping (--stop-on-error)")
            break

    ran = {r[0] for r in results}
    summary = [f"fff daily started {started:%Y-%m-%d %H:%M:%S}, environment args: {_global_args(args) or 'default'}"]
    for label, argv, code, secs in results:
        summary.append(f"  {'OK  ' if code == 0 else 'FAIL'} {label:<30} exit {code:<4} {secs / 60:6.1f} min  (fff {' '.join(argv)})")
    for _, argv, label in stages:
        if label not in ran:
            summary.append(f"  SKIP {label:<30} not run (stopped earlier)")
    text = "\n".join(summary)
    print(f"\n{text}")

    log_dir = paths.LOG_DIR / "daily"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{started:%Y%m%d_%H%M%S}.log").write_text(text + "\n", encoding="utf-8")

    failed = [r for r in results if r[2] != 0]
    return 1 if failed or len(results) < len(stages) else 0
