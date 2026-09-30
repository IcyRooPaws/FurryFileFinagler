"""``fff`` command-line entry point.

Command groups are registered here as each phase of project notes lands.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fff import __version__
from fff.common import config as fff_config
from fff.common import paths


def _cmd_config_examples(args) -> int:
    from fff.common import config_examples

    workspace = Path(args.from_workspace) if args.from_workspace else paths.ROOT.parent
    sources = config_examples.Sources(workspace)
    missing = [p for p in (sources.fluffle, sources.archive, sources.sorter) if not p.is_dir()]
    if missing:
        print("Original project folder(s) not found: " + ", ".join(map(str, missing)), file=sys.stderr)
        return 2
    result = config_examples.build(sources)
    for warning in result.warnings:
        print(f"NOTE  {warning}")
    for action in config_examples.write(result, force=args.force, dry_run=args.dry_run):
        print(action)
    if args.dry_run:
        print("(dry run: nothing written)")
    return 0


def _cmd_config_make_example(args) -> int:
    import yaml

    from fff.common import config_examples

    with open(paths.CONFIG_DIR / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}  # config.yaml only, never secrets.yaml
    print(config_examples.write_example(cfg, force=args.force))
    return 0


def _cmd_config_show(args) -> int:
    import yaml

    cfg = fff_config.load()
    data = fff_config.legacy_view(args.module, cfg) if args.module else cfg

    def redact(obj):
        if isinstance(obj, dict):
            return {k: ("***" if k in ("password", "api_key", "login") and v else redact(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [redact(v) for v in obj]
        return obj

    yaml.safe_dump(redact(data), sys.stdout, sort_keys=False, allow_unicode=True, width=120)
    return 0


def _cmd_doctor(args) -> int:
    from fff.cli import doctor

    return doctor.run(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fff", description="FurryFileFinagler")
    parser.add_argument("--version", action="version", version=f"fff {__version__}")
    parser.add_argument("--config", help="Path to config.yaml (default: config/config.yaml)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # config
    p_config = sub.add_parser("config", help="Build or inspect the unified configuration")
    config_sub = p_config.add_subparsers(dest="config_command", metavar="<action>", required=True)
    p = config_sub.add_parser("migrate", help="Build config.yaml/secrets.yaml from the original three projects")
    p.add_argument("--from-workspace", help="Folder containing the current features (default: parent of this project)")
    p.add_argument("--force", action="store_true", help="Overwrite existing files")
    p.add_argument("--dry-run", action="store_true", help="Show what would be written")
    p.set_defaults(func=_cmd_config_examples, needs_config=False)
    p = config_sub.add_parser("make-example", help="Write config/config.example.yaml from config.yaml, sanitized")
    p.add_argument("--force", action="store_true", help="Overwrite an existing example")
    p.set_defaults(func=_cmd_config_make_example)
    p = config_sub.add_parser("show", help="Print the config (secrets redacted)")
    p.add_argument("--module", choices=fff_config.MODULES,
                   help="Show the legacy view one module's ported code sees")
    p.set_defaults(func=_cmd_config_show)

    # doctor
    p = sub.add_parser("doctor", help="Check databases, folders and dependencies")
    p.add_argument("--skip-db", action="store_true", help="Don't connect to MySQL")
    p.add_argument("--skip-deps", action="store_true", help="Don't check Python packages")
    p.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    if getattr(args, "needs_config", True):
        try:
            fff_config.load(args.config) if args.config else fff_config.load()
        except fff_config.ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
