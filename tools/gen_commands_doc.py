"""Regenerate docs/COMMANDS.md from the CLI parsers."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fff.cli.commands_doc import render  # noqa: E402

(ROOT / "docs" / "COMMANDS.md").write_text(render(), encoding="utf-8", newline="\n")
print("wrote docs/COMMANDS.md")
