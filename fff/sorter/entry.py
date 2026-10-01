"""``fff sort ...``: media sorter's main.py sub-commands, plus run.py and fa_backlog_sort.

``fff sort <command>`` runs exactly what ``python main.py <command>`` ran (same 14
sub-commands, flags and defaults); ``fff sort workflow`` is run.py and ``fff sort
fa-backlog`` is fa_backlog_sort.py.
"""
import importlib
import sys

FORWARD_HELP_SAFE = True  # --help is handled by argparse (main.py / run.py parsers); never runs a command

EXTRA = {
    "workflow": ("fff.sorter.workflow", "Full workflow: deleted posts, tag sort, sort+hash, artists, archive, "
                                        "maintenance, cleanup (was run.py)"),
    "fa-backlog": ("fff.sorter.scripts.fa_backlog_sort", "Move -FA- files into their 'Furaffinity - <Rating>' "
                                                         "folders (was fa_backlog_sort.py)"),
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in EXTRA:
        module = importlib.import_module(EXTRA[argv[0]][0])
        return module.main(argv[1:]) or 0
    from fff.sorter import main as sorter_main

    if not argv or argv[0] in ("-h", "--help"):
        sorter_main.build_parser().print_help()
        print("\nalso:")
        for name, (_, text) in EXTRA.items():
            print(f"  {name:<18} {text}")
        return 0
    return sorter_main.main(argv)
