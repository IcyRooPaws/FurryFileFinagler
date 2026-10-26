"""One-time user consent for things FFF does outside its own folders.

Answers are kept in ``data/consent.json`` so each one is only asked once. Only an
agreement is saved: declining just means FFF asks again next time.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

from fff.common import paths

CONSENT_FILE = paths.DATA_DIR / "consent.json"

BROWSER_COOKIES = "browser_cookies"

PROMPTS = {
    BROWSER_COOKIES: (
        "FurAffinity features (faving, unfaving, downloading) sign in as you by reading your\n"
        "FurAffinity cookies from your Firefox-based browser (Waterfox, Firefox, LibreWolf, Floorp\n"
        "or Zen). Only cookies for furaffinity.net are read, they stay on this computer, and they\n"
        "are only sent to furaffinity.net. Nothing else in your browser profile is read."
    ),
}


def _load() -> dict:
    try:
        with open(CONSENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def has_consent(key: str) -> bool:
    entry = _load().get(key)
    return isinstance(entry, dict) and bool(entry.get("granted"))


def set_consent(key: str, granted: bool) -> None:
    """Record (``granted=True``) or revoke (``granted=False``) consent for ``key``."""
    data = _load()
    if granted:
        data[key] = {"granted": True, "at": datetime.now().isoformat(timespec="seconds")}
    else:
        data.pop(key, None)
    CONSENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONSENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def require_consent(key: str) -> bool:
    """True once the user has agreed to ``key``, asking in the console the first time.

    Without an interactive console (scheduled runs, Manager actions) nothing is asked:
    this explains how to agree and returns False.
    """
    if has_consent(key):
        return True
    print()
    print(PROMPTS[key])
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        print(f"Not agreed to yet, so this step is skipped. Agree by opening the FFF Manager "
              f"or running: fff consent {key.replace('_', '-')}")
        return False
    try:
        answer = input("Allow this? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer in ("y", "yes"):
        set_consent(key, True)
        print(f"Saved. You won't be asked again (undo with: fff consent {key.replace('_', '-')} --revoke)")
        return True
    print("Not allowed; this step is skipped.")
    return False


def ask_in_dialog(key: str, parent=None) -> bool:
    """GUI version of :func:`require_consent`: a yes/no dialog, shown only if not agreed yet."""
    if has_consent(key):
        return True
    from tkinter import messagebox

    if messagebox.askyesno("FFF permission", PROMPTS[key] + "\n\nAllow this?", parent=parent):
        set_consent(key, True)
        return True
    return False
