"""fff daily: runs the three pipelines in order, as separate fff processes."""
import sys
from types import SimpleNamespace

import pytest

from fff.cli import daily
from fff.cli.app import build_parser
from fff.common import paths


def parse(*argv):
    return build_parser().parse_args(list(argv))


class FakeRunner:
    def __init__(self, codes=None):
        self.calls, self.codes = [], list(codes or [])

    def __call__(self, cmd):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=self.codes.pop(0) if self.codes else 0)

    def stages(self):
        prefix = [sys.executable, "-m", "fff"]
        return [c[len(prefix):] for c in self.calls]


@pytest.fixture(autouse=True)
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "logs")
    return tmp_path / "logs" / "daily"


def test_runs_standard_stage_order():
    """Search, sorting, archive sync, sorting, then external source sync."""
    run = FakeRunner()
    assert daily.run(parse("daily"), runner=run) == 0
    assert run.stages() == [["fluffle", "search"], ["sort", "workflow"], ["e621", "sync"],
                            ["sort", "workflow"], ["fa", "sync-external"]]


def test_skips_and_skip_favorites():
    run = FakeRunner()
    assert daily.run(parse("daily", "--skip-fluffle", "--skip-fa-sync", "--skip-favorites"), runner=run) == 0
    assert run.stages() == [["sort", "workflow"], ["e621", "sync", "--skip-favorites"], ["sort", "workflow"]]
    run = FakeRunner()
    assert daily.run(parse("daily", "--skip-e621", "--skip-fluffle", "--skip-sort", "--skip-fa-sync"), runner=run) == 0
    assert run.calls == []


def test_env_and_config_pass_through():
    run = FakeRunner()
    daily.run(parse("--env", "test", "--config", "x.yaml", "daily", "--skip-fluffle", "--skip-sort", "--skip-fa-sync"),
              runner=run)
    assert run.stages() == [["--env", "test", "--config", "x.yaml", "e621", "sync"]]


def test_failure_keeps_going_by_default_but_exits_nonzero(log_dir):
    run = FakeRunner(codes=[0, 0, 1, 0, 0])  # A failed stage does not stop later stages by default.
    assert daily.run(parse("daily"), runner=run) == 1
    assert len(run.calls) == 5
    summary = next(log_dir.glob("*.log")).read_text(encoding="utf-8")
    assert "FAIL e621 favorites sync" in summary and "OK   Library workflow (2nd)" in summary


def test_stop_on_error():
    run = FakeRunner(codes=[0, 2, 0])
    assert daily.run(parse("daily", "--stop-on-error"), runner=run) == 1
    assert run.stages() == [["fluffle", "search"], ["sort", "workflow"]]


def test_dry_run_runs_nothing(capsys):
    run = FakeRunner()
    assert daily.run(parse("--env", "test", "daily", "--dry-run"), runner=run) == 0
    assert run.calls == []
    out = capsys.readouterr().out
    assert "1. Reverse-image source search: fff --env test fluffle search" in out
    assert "5. External FA sources sync: fff --env test fa sync-external" in out


@pytest.mark.parametrize("argv", sorted({tuple(s[1]) for s in daily.STAGES}), ids=lambda a: " ".join(a))
def test_each_stage_is_a_real_command(argv, capsys):
    """The stage commands must exist in the CLI (a typo would only show up at 3 a.m.)."""
    from fff.cli.app import main

    try:
        code = main(list(argv) + ["--help"])
    except SystemExit as exc:  # commands with options declared in fff exit from argparse
        code = exc.code
    assert code == 0
    assert "usage:" in capsys.readouterr().out
