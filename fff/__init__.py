"""FurryFileFinagler media archiving, source search, and library management toolkit."""

# The only place the version is set; pyproject.toml reads it. Every commit gets one, with a
# matching entry at the top of CHANGELOG.md (tests/test_docs.py checks they agree).
__version__ = "1.34.1"

# Nothing is ever moved, renamed or deleted out of a protected_directories entry, by any code path.
from fff.common import protected as _protected  # noqa: E402

_protected.install()
