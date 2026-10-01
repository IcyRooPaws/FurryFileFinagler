"""Docs stay in sync with the code and never carry private values."""
import re

from fff.common import paths


def test_commands_doc_is_up_to_date():
    from fff.cli.commands_doc import render

    committed = (paths.ROOT / "docs" / "COMMANDS.md").read_text(encoding="utf-8")
    assert committed == render(), "run: python tools/gen_commands_doc.py"


def test_docs_contain_no_private_ip_addresses():
    private = re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}\b")
    offenders = [str(p.relative_to(paths.ROOT)) for p in (paths.ROOT / "docs").rglob("*.md")
                 if private.search(p.read_text(encoding="utf-8"))]
    offenders += [name for name in ("README.md",)
                  if private.search((paths.ROOT / name).read_text(encoding="utf-8"))]
    assert not offenders, offenders


def test_plan_document_is_not_shipped():
    assert not (paths.ROOT / "project notes").exists()
