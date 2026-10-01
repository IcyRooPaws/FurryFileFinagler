"""``fff hash ...``: the v1 media_hasher CLI (FFF/_handlers/media_hasher/cli.py).

Its main() parses sys.argv itself, so argv is passed through sys.argv here; the CLI
argparse (``fff hash --help``) is unchanged.
"""
import sys

FORWARD_HELP_SAFE = True  # --help is handled by the v1 CLI argparse

from fff.sorter.handlers.media_hasher import cli


def main(argv=None) -> int:
    saved = sys.argv
    sys.argv = ["fff hash"] + list(sys.argv[1:] if argv is None else argv)
    try:
        return cli.main() or 0
    finally:
        sys.argv = saved
