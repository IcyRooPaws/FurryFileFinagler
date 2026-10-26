"""Consent to read browser cookies is asked once, saved, and required before any cookie is read."""
import io

import pytest

from fff.clients import furaffinity
from fff.common import consent


@pytest.fixture(autouse=True)
def consent_file(tmp_path, monkeypatch):
    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent, "CONSENT_FILE", path)
    return path


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_agreeing_is_saved_and_not_asked_again(consent_file, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Tty("y\n"))
    assert consent.require_consent(consent.BROWSER_COOKIES)
    assert consent_file.exists()

    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked twice"))
    assert consent.require_consent(consent.BROWSER_COOKIES)


def test_declining_is_not_saved(consent_file, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Tty("n\n"))
    assert not consent.require_consent(consent.BROWSER_COOKIES)
    assert not consent.has_consent(consent.BROWSER_COOKIES)


def test_non_interactive_run_skips_without_asking(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked without a console"))
    assert not consent.require_consent(consent.BROWSER_COOKIES)
    assert "fff consent browser-cookies" in capsys.readouterr().out


def test_revoke(monkeypatch):
    consent.set_consent(consent.BROWSER_COOKIES, True)
    consent.set_consent(consent.BROWSER_COOKIES, False)
    assert not consent.has_consent(consent.BROWSER_COOKIES)


def test_cookies_are_not_read_without_consent(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr(furaffinity, "_browser_profile_dirs",
                        lambda: pytest.fail("browser profiles read without consent"))
    assert furaffinity.get_firefox_cookies() is None


def test_user_agent_matches_the_browser_version(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    (tmp_path / "compatibility.ini").write_text("[Compatibility]\nLastVersion=153.5.0_20261010/20261010\n")
    assert furaffinity._profile_user_agent(tmp_path) == (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0")

    (tmp_path / "prefs.js").write_text('user_pref("general.useragent.override", "Custom/1.0");\n')
    assert furaffinity._profile_user_agent(tmp_path) == "Custom/1.0"
