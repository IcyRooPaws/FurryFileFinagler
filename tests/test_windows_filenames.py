"""Windows-safe e621 file names: invalid characters become '_' (2026-10-01)."""
import re

import pytest

from fff.common import paths
from fff.common.naming import windows_safe_filename
from tests.test_windows_filenames import NAMES

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@pytest.mark.parametrize("name,expected", [
    # the post that failed to download every night ([Errno 22] Invalid argument)
    ('42^02\'40.2"n-87^40\'53.8"w_fops-questionable-6129891.png',
     "42^02'40.2_n-87^40'53.8_w_fops-questionable-6129891.png"),
    ("a<b>c:d|e?f*g-safe-1.png", "a_b_c_d_e_f_g-safe-1.png"),
    ("slash/back\\slash-explicit-2.jpg", "slash_back_slash-explicit-2.jpg"),
    ("tab\there-explicit-3.png", "tab_here-explicit-3.png"),
    ("trailing dot.", "trailing dot_"),
    ("CON.png", "_CON.png"),
    ("con-safe-1.png", "con-safe-1.png"),  # only the exact device name is reserved
    ("", ""),
    (None, None),
])
def test_examples(name, expected):
    assert windows_safe_filename(name) == expected


def test_valid_names_are_unchanged_and_result_is_always_valid():
    for name in NAMES:
        out = windows_safe_filename(name)
        assert not INVALID.search(out or "")
        if not INVALID.search(name) and not name.endswith((" ", ".")):
            assert out == name, name
        assert windows_safe_filename(out) == out  # idempotent


def test_every_archive_file_name_builder_applies_it():
    """Each place fff/archive builds a post file name must end with windows_safe_filename."""
    src = (paths.ROOT / "fff" / "archive" / "db.py").read_text(encoding="utf-8").splitlines()
    builders = [i for i, line in enumerate(src)
                if re.search(r'(file_name|new_file_name|expected_file_name) = f"\{artist_part\}-', line)]
    assert len(builders) == 5
    for i in builders:
        window = "\n".join(src[i:i + 6])
        assert "windows_safe_filename(" in window, f"db.py line {i + 1} builds a file name without it"
    download = (paths.ROOT / "fff" / "archive" / "download.py").read_text(encoding="utf-8")
    assert "windows_safe_filename(strip_flag_tokens(file_name))" in download
