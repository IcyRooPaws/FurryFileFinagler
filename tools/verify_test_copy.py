"""TEMPORARY: check that tools/copy_prod_to_test.py copied every prod file to the test tree.

For each top-level prod folder, compares the file set (relative paths) and total bytes with
its test copy. Only reads both sides.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from copy_prod_to_test import folder_pairs, human  # noqa: E402
from fff.common import config as fff_config  # noqa: E402


def inventory(root):
    files = {}
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files[os.path.relpath(p, root).lower()] = (st.st_size, int(st.st_mtime))
    return files


def main():
    roots, _ = folder_pairs(fff_config.load())
    problems = 0
    for prod, test, _ in roots:
        if not os.path.isdir(prod):
            continue
        a, b = inventory(prod), inventory(test)
        missing = [k for k in a if k not in b]
        differ = [k for k in a if k in b and b[k][0] != a[k][0]]
        extra = [k for k in b if k not in a]
        ok = not missing and not differ
        problems += not ok
        print(f"{'OK ' if ok else 'BAD'} {prod}: prod {len(a)} files {human(sum(v[0] for v in a.values()))}, "
              f"test {len(b)} files {human(sum(v[0] for v in b.values()))}"
              + (f"; missing {len(missing)}" if missing else "") + (f"; size differs {len(differ)}" if differ else "")
              + (f"; extra in test {len(extra)}" if extra else ""))
        for k in (missing + differ)[:5]:
            print(f"      {k}")
    print(f"\n{problems} folder(s) with problems." if problems else "\nEvery prod file has a same-size copy in the test tree.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
