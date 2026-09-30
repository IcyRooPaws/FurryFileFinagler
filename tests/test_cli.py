import pytest

from fff.cli.app import build_parser


def _help_targets(parser, prefix=()):
    """Yield argv prefixes for every (sub)command in the parser tree."""
    yield list(prefix)
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            for name, sub in action.choices.items():
                yield from _help_targets(sub, prefix + (name,))


@pytest.mark.parametrize("argv", list(_help_targets(build_parser())), ids=lambda a: " ".join(a) or "fff")
def test_every_command_has_help(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv + ["--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out
