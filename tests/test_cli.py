import pytest

from fff.cli.app import build_parser


def _help_targets(parser, prefix=()):
    """Yield argv prefixes for every (sub)command in the parser tree."""
    yield list(prefix)
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            for name, sub in action.choices.items():
                if sub.add_help:  # forwarded commands are covered by test_archive_port.py
                    yield from _help_targets(sub, prefix + (name,))


@pytest.mark.parametrize("argv", list(_help_targets(build_parser())), ids=lambda a: " ".join(a) or "fff")
def test_every_command_has_help(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv + ["--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def _subcommands(parser):
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return sorted(set(action.choices))
    return []


def _sort_targets():
    from fff.sorter.main import build_parser as sorter_parser

    return [["sort", name] for name in _subcommands(sorter_parser())] + [["sort", "workflow"], ["sort", "fa-backlog"]]


def _hash_targets():
    import argparse

    from fff.sorter.handlers.media_hasher import cli

    captured = {}
    real_parse = argparse.ArgumentParser.parse_args

    def grab(self, *a, **k):  # build the v1 CLI's parser without running it
        captured["parser"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = grab
    try:
        try:
            cli.main()
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real_parse
    return [["hash", name] for name in _subcommands(captured["parser"])]


@pytest.mark.parametrize("argv", _sort_targets() + _hash_targets(), ids=lambda a: " ".join(a))
def test_every_sorter_and_hash_subcommand_has_help(argv, capsys):
    from fff.cli.app import main

    assert main(argv + ["--help"]) == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["e621", "sync"], ["sort", "workflow"], ["hash", "info"]])
def test_forwarded_help_works_without_config(argv, monkeypatch, capsys):
    """A fresh checkout (or CI) has no config.yaml; --help must still work."""
    from fff.cli.app import main
    from fff.common import config as fff_config

    def missing(*a, **k):
        raise fff_config.ConfigError("config.yaml not found")

    monkeypatch.setattr(fff_config, "load", missing)
    try:
        code = main(argv + ["--help"])
    except SystemExit as exc:
        code = exc.code
    assert code == 0
    assert "usage:" in capsys.readouterr().out
