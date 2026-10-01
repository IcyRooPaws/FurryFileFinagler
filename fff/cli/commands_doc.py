"""Generate docs/COMMANDS.md from the real parsers (python tools/gen_commands_doc.py).

tests/test_docs.py fails when the committed file is out of date.
"""
import argparse

from fff.cli import archive_cmds
from fff.cli.app import build_parser

CORE = ("config", "db", "doctor", "setup")


def _subparsers(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            help_by_name = {a.dest: a.help for a in action._choices_actions}
            return [(name, p, help_by_name.get(name, "")) for name, p in action.choices.items()]
    return []


def _walk(parser, prefix):
    for name, sub, text in _subparsers(parser):
        path = prefix + (name,)
        children = _subparsers(sub)
        if children:
            yield from _walk(sub, path)
        else:
            yield path, text or (sub.description or "").split("\n")[0]


def _md(text):
    return (text or "").replace("|", "/").replace("\n", " ").strip()


def render() -> str:
    parser = build_parser()
    ported = {c.path: c for c in archive_cmds.COMMANDS}
    lines = ["# fff command reference", "",
             "Generated from the parsers by `python tools/gen_commands_doc.py`; do not edit by hand.",
             "Global options: `fff [--config PATH] [--env prod|test] <command>`.", "",
             "## General commands", "", "| Command | What it does |", "|---|---|"]
    for path, text in _walk(parser, ()):
        if path[0] in CORE and path not in ported:
            lines.append(f"| `fff {' '.join(path)}` | {_md(text)} |")

    lines += ["", "## Archive and Media Commands", "",
              "| Command | What it does |", "|---|---|"]
    for c in archive_cmds.COMMANDS:
        lines.append(f"| `fff {' '.join(c.path)}` | {_md(c.help)} |")

    from fff.sorter.main import build_parser as sorter_parser
    from fff.sorter.entry import EXTRA

    lines += ["", "## `fff sort` Sub-commands", "",
              "Sorting, archiving, indexing, and library maintenance commands.", "",
              "| Command | What it does |", "|---|---|"]
    for name, _sub, text in sorted(_subparsers(sorter_parser()), key=lambda t: t[0]):
        lines.append(f"| `fff sort {name}` | {_md(text)} |")
    for name, (_module, text) in EXTRA.items():
        lines.append(f"| `fff sort {name}` | {_md(text)} |")
    return "\n".join(lines) + "\n"
