"""TEMPORARY: copy every configured prod folder (and its files) to its test location.

Prod folders are only read. Each is copied to where the test environment reroutes it
(P:/IPL/x -> E:/Testing/P/IPL/x, see ``fff config test-dirs``).

    python tools/copy_prod_to_test.py                     # dry run: sizes, free space, plan
    python tools/copy_prod_to_test.py --execute           # copy
    python tools/copy_prod_to_test.py --execute --only "P:/IPL/- Need to be Sorted"

* Nested folders are collapsed (P:/IPL/Pictures is covered by P:/IPL), so nothing is copied twice.
* Files already at the destination with the same size and mtime are skipped, so a copy can
  be stopped and resumed.
* Timestamps are preserved (shutil.copy2). Folders that don't exist in prod are skipped.
* Refuses to start if the total doesn't fit in the test drive's free space (unless --force).
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fff.cli import doctor  # noqa: E402
from fff.common import config as fff_config  # noqa: E402


def folder_pairs(cfg):
    prod = fff_config.deep_merge(cfg, {"environment": "prod"})
    test = fff_config.deep_merge(cfg, {"environment": "test"})
    fff_config.set_environment(None)
    pairs = {}
    for (label, p), (_, t) in zip(doctor.configured_directories(prod), doctor.configured_directories(test)):
        if p != t:
            pairs.setdefault(os.path.normcase(os.path.normpath(p)), (p, t, label))
    # Collapse nested folders: keep only roots not inside another configured prod folder.
    keys = sorted(pairs, key=len)
    roots = []
    for k in keys:
        if not any(k == r or k.startswith(r.rstrip("\\/") + os.sep) for r in roots):
            roots.append(k)
    return [pairs[k] for k in roots], len(pairs)


def measure(folder):
    files = size = 0
    for dirpath, _, filenames in os.walk(folder):
        for name in filenames:
            try:
                size += os.path.getsize(os.path.join(dirpath, name))
                files += 1
            except OSError:
                pass
    return files, size


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024


def copy_tree(src, dest, stats):
    for dirpath, _, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target_dir = os.path.join(dest, rel) if rel != "." else dest
        os.makedirs(target_dir, exist_ok=True)
        for name in filenames:
            s, d = os.path.join(dirpath, name), os.path.join(target_dir, name)
            try:
                st = os.stat(s)
                if os.path.exists(d):
                    dt = os.stat(d)
                    if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                        stats["skipped"] += 1
                        continue
                shutil.copy2(s, d)
                stats["copied"] += 1
                stats["bytes"] += st.st_size
            except OSError as e:
                stats["errors"] += 1
                print(f"  ERROR {s}: {e}")
            if (stats["copied"] + stats["skipped"]) % 1000 == 0:
                print(f"  ... {stats['copied']} copied, {stats['skipped']} skipped, {human(stats['bytes'])}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="Actually copy (default: dry run)")
    ap.add_argument("--only", action="append", help="Only this prod folder (repeatable)")
    ap.add_argument("--force", action="store_true", help="Copy even if it may not fit")
    args = ap.parse_args(argv)

    cfg = fff_config.load()
    roots, total_configured = folder_pairs(cfg)
    if args.only:
        # Any prod folder, including one inside a configured folder (e.g. P:/IPL/- Need to be Sorted)
        test_cfg = fff_config.deep_merge(cfg, {"environment": "test"})
        roots = [(o, fff_config.test_path(o, test_cfg), "--only") for o in args.only]
    print(f"{total_configured} configured folders -> {len(roots)} top-level folders to copy")
    print(f"Test root: {fff_config.test_paths_root(cfg)}\n")

    plan, grand = [], 0
    for prod, test, label in roots:
        if not os.path.isdir(prod):
            print(f"  SKIP (missing in prod) {prod}")
            continue
        start = time.time()
        files, size = measure(prod)
        grand += size
        plan.append((prod, test))
        print(f"  {human(size):>10}  {files:>8} files  {prod}\n{'':>32}-> {test}   [{label}, {time.time() - start:.0f}s]")

    test_root = fff_config.test_paths_root(cfg)
    anchor = Path(test_root).anchor or test_root
    free = shutil.disk_usage(anchor).free if os.path.exists(anchor) else 0
    print(f"\nTotal {human(grand)}; free on {anchor}: {human(free)}")
    if not args.execute:
        print("Dry run: nothing copied. Re-run with --execute (optionally --only <folder>).")
        return 0
    if grand > free and not args.force:
        print("Not enough free space; use --only to copy part of it, or --force.")
        return 1
    stats = {"copied": 0, "skipped": 0, "errors": 0, "bytes": 0}
    for prod, test in plan:
        print(f"\nCopying {prod} -> {test}")
        copy_tree(prod, test, stats)
    print(f"\nDone: {stats['copied']} copied ({human(stats['bytes'])}), {stats['skipped']} already there, "
          f"{stats['errors']} errors.")
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
