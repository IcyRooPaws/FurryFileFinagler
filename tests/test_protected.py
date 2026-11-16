import os
import pathlib
import shutil

import pytest

import fff  # noqa: F401  (installs the guard)
from fff.common import fileops, protected


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A protected directory holding one file, plus an unprotected directory beside it."""
    monkeypatch.setattr(protected, "_dirs", None)
    safe = tmp_path / "Commissioned Art"
    (safe / "sub").mkdir(parents=True)
    (safe / "sub" / "piece.png").write_bytes(b"art")
    other = tmp_path / "Fav"
    other.mkdir()
    protected.set_directories([str(safe)])
    return safe, other


def test_guard_is_installed():
    assert getattr(shutil.move, "__fff_protected__", False)
    assert getattr(os.rename, "__fff_protected__", False)


def test_is_protected_matches_dir_and_children_only(vault):
    safe, other = vault
    assert protected.is_protected(safe)
    assert protected.is_protected(safe / "sub" / "piece.png")
    if os.name == "nt":  # Windows paths are case-insensitive
        assert protected.is_protected(str(safe / "sub" / "piece.png").upper())
    assert not protected.is_protected(str(safe) + "work/x.png")  # sibling with a shared prefix
    assert not protected.is_protected(other / "x.png")


@pytest.mark.parametrize("op", [
    lambda src, dst: shutil.move(src, dst),
    lambda src, dst: shutil.move(src, os.path.dirname(dst)),
    lambda src, dst: os.rename(src, dst),
    lambda src, dst: os.replace(src, dst),
    lambda src, dst: pathlib.Path(src).rename(dst),
    lambda src, dst: pathlib.Path(src).replace(dst),
    lambda src, dst: os.remove(src),
    lambda src, dst: os.unlink(src),
    lambda src, dst: pathlib.Path(src).unlink(),
    lambda src, dst: shutil.rmtree(os.path.dirname(src)),
    lambda src, dst: os.rename(src, src + ".renamed"),  # in-place rename
])
def test_nothing_leaves_a_protected_directory(vault, op):
    safe, other = vault
    src = str(safe / "sub" / "piece.png")
    with pytest.raises(protected.ProtectedPathError):
        op(src, str(other / "piece.png"))
    assert os.path.exists(src)


def test_move_file_with_logging_refuses(vault):
    safe, other = vault
    src = safe / "sub" / "piece.png"
    with pytest.raises(PermissionError):
        fileops.move_file_with_logging(str(src), str(other / "piece.png"))
    assert src.exists()


def test_moving_in_and_copying_out_still_work(vault):
    safe, other = vault
    shutil.copy2(safe / "sub" / "piece.png", other / "copy.png")
    (other / "new.png").write_bytes(b"new")
    shutil.move(str(other / "new.png"), str(safe / "new.png"))
    assert (safe / "new.png").read_bytes() == b"new"
    assert (other / "copy.png").exists()


def test_cannot_overwrite_a_protected_file(vault):
    safe, other = vault
    (other / "piece.png").write_bytes(b"impostor")
    with pytest.raises(protected.ProtectedPathError):
        os.replace(other / "piece.png", safe / "sub" / "piece.png")
    with pytest.raises(protected.ProtectedPathError):
        shutil.move(str(other / "piece.png"), str(safe / "sub"))
    assert (safe / "sub" / "piece.png").read_bytes() == b"art"


def test_unprotected_paths_untouched(vault):
    _, other = vault
    (other / "a.png").write_bytes(b"x")
    os.rename(other / "a.png", other / "b.png")
    os.remove(other / "b.png")
    assert not any(other.iterdir())
