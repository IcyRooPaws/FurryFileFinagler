"""``fff`` command-line entry point."""
from __future__ import annotations

import argparse
import sys

from fff import __version__
from fff.common import config as fff_config
from fff.common import paths


def _cmd_config_make_example(args) -> int:
    import yaml

    from fff.common import config_examples

    with open(paths.CONFIG_DIR / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}  # config.yaml only, never secrets.yaml
    print(config_examples.write_example(cfg, force=args.force))
    return 0


def _cmd_config_test_dirs(args) -> int:
    from pathlib import Path

    from fff.cli import doctor

    cfg = fff_config.load()
    prod = fff_config.deep_merge(cfg, {"environment": "prod"})
    test = fff_config.deep_merge(cfg, {"environment": "test"})
    saved = fff_config._env_override
    fff_config.set_environment(None)  # compare both environments regardless of --env/FFF_ENV
    try:
        pairs = list(zip(doctor.configured_directories(prod), doctor.configured_directories(test)))
    finally:
        fff_config.set_environment(saved)
    print(f"Test root: {fff_config.test_paths_root(test)}")
    seen, created = set(), 0
    for (label, prod_dir), (_, test_dir) in pairs:
        if test_dir in seen:
            continue
        seen.add(test_dir)
        exists = Path(test_dir).exists()
        if args.create and not exists and test_dir != prod_dir:
            Path(test_dir).mkdir(parents=True, exist_ok=True)
            created += 1
            exists = True
        state = "same as prod!" if test_dir == prod_dir else ("exists" if exists else "missing")
        print(f"  {state:<13} {test_dir}")
        print(f"  {'':<13} <- {prod_dir}   [{label}]")
    if args.create:
        print(f"Created {created} folder(s).")
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


def _cmd_db_copy_schema(args) -> int:
    import logging

    from fff.common import schema_copy
    from fff.common.logging import setup_logging

    log_file = setup_logging("db", "DEBUG" if args.verbose else "INFO", name="copy_schema")
    cfg = fff_config.load()
    pairs = schema_copy.test_database_pairs(cfg, args.db)
    print("Will copy (structure only, no data): " + ", ".join(f"{p} -> {t}" for _, p, t in pairs))
    if args.dry_run:
        logging.info("DRY RUN MODE - No changes will be made")
    ok = schema_copy.copy_all(cfg, args.db, dry_run=args.dry_run)
    print(f"{'Done' if ok else 'Finished with errors'}. Log: {log_file}")
    return 0 if ok else 1


def _cmd_db_copy_data(args) -> int:
    import logging

    from fff.common import schema_copy
    from fff.common.logging import setup_logging

    log_file = setup_logging("db", "DEBUG" if args.verbose else "INFO", name="copy_data")
    cfg = fff_config.load()
    pairs = schema_copy.test_database_pairs(cfg, args.db)
    print("Will REPLACE the contents of: " + ", ".join(f"{t} (from {p})" for _, p, t in pairs))
    if not args.dry_run and not args.yes:
        if input("Type 'copy' to continue: ").strip().lower() != "copy":
            print("Cancelled.")
            return 1
    if args.dry_run:
        logging.info("DRY RUN MODE - No changes will be made")
    ok = schema_copy.copy_all_data(cfg, args.db, dry_run=args.dry_run)
    print(f"{'Done' if ok else 'Finished with errors'}. Log: {log_file}")
    return 0 if ok else 1


def _cmd_db_rewrite_test_paths(args) -> int:
    from fff.common import test_db_paths
    from fff.common.logging import setup_logging

    log_file = setup_logging("db", "INFO", name="rewrite_test_paths")
    cfg = fff_config.load()
    results = test_db_paths.rewrite(cfg, args.db, dry_run=args.dry_run)
    total = sum(results.values())
    for key, n in results.items():
        if n:
            print(f"  {n:>8}  {key}")
    print(f"{'Would rewrite' if args.dry_run else 'Rewrote'} {total} stored paths. Log: {log_file}")
    if not args.dry_run:
        left = test_db_paths.remaining_prod_paths(cfg, args.db)
        print("All stored paths now point at test locations." if not left else f"Still pointing at prod: {left}")
        return 1 if left else 0
    return 0


def _cmd_doctor(args) -> int:
    from fff.cli import doctor

    return doctor.run(args)


def _cmd_setup(args) -> int:
    from fff.gui import setup_ui

    return setup_ui.main(args.config)


def _cmd_consent(args) -> int:
    from fff.common import consent

    key = args.what.replace("-", "_")
    if args.revoke:
        consent.set_consent(key, False)
        print(f"Revoked. FFF will ask again before using {args.what}.")
        return 0
    if args.status:
        print("Agreed" if consent.has_consent(key) else "Not agreed")
        return 0
    print(consent.PROMPTS[key])
    consent.set_consent(key, True)
    print(f"\nAgreed and saved to {consent.CONSENT_FILE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fff", description="FurryFileFinagler")
    parser.add_argument("--version", action="version", version=f"fff {__version__}")
    parser.add_argument("--config", help="Path to config.yaml (default: config/config.yaml)")
    parser.add_argument("--env", choices=fff_config.ENVIRONMENTS,
                        help="Use prod or test_* databases, overriding FFF_ENV and config.yaml's 'environment'")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # config
    p_config = sub.add_parser("config", help="Build or inspect the unified configuration")
    config_sub = p_config.add_subparsers(dest="config_command", metavar="<action>", required=True)
    p = config_sub.add_parser("make-example", help="Write config/config.example.yaml from config.yaml, sanitized")
    p.add_argument("--force", action="store_true", help="Overwrite an existing example")
    p.set_defaults(func=_cmd_config_make_example)
    p = config_sub.add_parser("test-dirs", help="Show where each configured folder goes in the test environment")
    p.add_argument("--create", action="store_true", help="Create the missing (empty) test folders")
    p.set_defaults(func=_cmd_config_test_dirs)
    p = config_sub.add_parser("show", help="Print the config (secrets redacted)")
    p.add_argument("--module", choices=fff_config.MODULES,
                   help="Show the module-specific configuration view")
    p.set_defaults(func=_cmd_config_show)

    # db
    p_db = sub.add_parser("db", help="Database tools")
    db_sub = p_db.add_subparsers(dest="db_command", metavar="<action>", required=True)
    p = db_sub.add_parser(
        "copy-schema",
        help="Create/refresh the test_* databases as empty copies of production's structure",
        description="Copies tables, views, triggers, procedures and functions (no data) from each "
                    "production database to test_<name>. Re-running drops and recreates the test "
                    "tables, which empties them. Production is only read.")
    p.add_argument("--db", nargs="+", choices=fff_config.MODULES, help="Only these modules (default: all three)")
    p.add_argument("--dry-run", action="store_true", help="Show what would be done")
    p.add_argument("--verbose", action="store_true", help="Log SQL statements")
    p.set_defaults(func=_cmd_db_copy_schema)
    p = db_sub.add_parser(
        "copy-data",
        help="Replace the test_* databases with a full copy of production (schema + rows)",
        description="Refreshes each test_<name> database's structure from production, then copies every "
                    "table's rows (server-side INSERT ... SELECT). Anything in the test databases is "
                    "replaced. Production is only read.")
    p.add_argument("--db", nargs="+", choices=fff_config.MODULES, help="Only these modules (default: all three)")
    p.add_argument("--dry-run", action="store_true", help="Show row counts without copying")
    p.add_argument("--yes", action="store_true", help="Don't ask for confirmation")
    p.add_argument("--verbose", action="store_true", help="Log SQL statements")
    p.set_defaults(func=_cmd_db_copy_data)
    p = db_sub.add_parser(
        "rewrite-test-paths",
        help="Point file paths stored in the test_* databases at the test tree (E:/Testing/...)",
        description="Rewrites every absolute path stored in the test_* databases with the same rule "
                    "the test environment uses for folders (P:/IPL/x -> E:/Testing/P/IPL/x), so test "
                    "runs never touch prod files. Run after 'fff db copy-data'. Production is untouched.")
    p.add_argument("--db", nargs="+", choices=fff_config.MODULES, help="Only these modules (default: all three)")
    p.add_argument("--dry-run", action="store_true", help="Count without changing anything")
    p.set_defaults(func=_cmd_db_rewrite_test_paths)

    # e621 / fa / gui / web / legacy commands
    from fff.cli import archive_cmds
    archive_cmds.register(sub)

    # daily
    from fff.cli import daily
    p = sub.add_parser("daily", help="Daily run: fluffle search, sort, e621 sync, sort, FA sync (like the .ps1)",
                       description=daily.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    daily.add_arguments(p)
    p.set_defaults(func=lambda args: daily.run(args))

    # first-run setup
    p = sub.add_parser("setup", help="Open the guided configuration and first-run checks")
    p.set_defaults(func=_cmd_setup, needs_config=False)

    # consent
    p = sub.add_parser("consent", help="Agree to (or revoke) FFF reading your FurAffinity browser cookies",
                       description="FFF asks once before reading FurAffinity cookies from your browser. "
                                   "This records that you agree, for scheduled runs that can't ask.")
    p.add_argument("what", choices=["browser-cookies"], help="What to agree to")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--revoke", action="store_true", help="Withdraw consent; FFF will ask again")
    group.add_argument("--status", action="store_true", help="Show whether you have agreed")
    p.set_defaults(func=_cmd_consent, needs_config=False)

    # doctor
    p = sub.add_parser("doctor", help="Check databases, folders and dependencies")
    p.add_argument("--skip-db", action="store_true", help="Don't connect to MySQL")
    p.add_argument("--skip-deps", action="store_true", help="Don't check Python packages")
    p.set_defaults(func=_cmd_doctor)

    return parser


def _utf8_console() -> None:
    """Some commands print and log non-ASCII characters; a cp1252 Windows console
    can't encode them, so use UTF-8 (replacing anything still unencodable) instead."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    args, extra = parser.parse_known_args(argv)
    forward_path = getattr(args, "forward_path", None)
    if forward_path is not None:
        from fff.cli import archive_cmds
        args.rest = archive_cmds.forwarded_argv(argv, forward_path)
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    fff_config.set_environment(args.env)
    # A forwarded command parses its own --help; printing help needs no config.yaml.
    wants_help = forward_path is not None and any(a in ("-h", "--help") for a in args.rest)
    if getattr(args, "needs_config", True) and not wants_help:
        try:
            cfg = fff_config.load(args.config) if args.config else fff_config.load()
            if fff_config.is_test(cfg):
                names = ", ".join(fff_config.database_names(cfg).values())
                print(f"[fff] TEST environment: using {names}", file=sys.stderr)
        except fff_config.ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
