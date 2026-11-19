"""Where e621 archive's files live in fff.

The original resolved these relative to its project folder or the CWD (config/, data/,
temp_responses/ ...). Folders it hard-coded outside the project are config settings now,
defaulting to the original values.
"""
from pathlib import Path

from fff.common import paths
from fff.common import config as fff_config

# Shared list files are stored under config/lists/.
AI_TAGS = paths.LISTS_DIR / "ai_tags.txt"
BLACKLIST = paths.LISTS_DIR / "blacklist.txt"
CRITICAL_BLACKLIST = paths.LISTS_DIR / "critical_blacklist.txt"
# Blacklist tags that do not count as hits in the review (other blacklisted tags still do)
BLACKLIST_IGNORE = paths.LISTS_DIR / "blacklist_ignore.txt"
REVIEW_ARTISTS = paths.CONFIG_DIR / "review_artists.yaml"
KEYBINDS = paths.CONFIG_DIR / "keybinds.yaml"

# data/: resolved on each access, so the test environment gets data/test/... (module
# __getattr__ below). Use them as archive_paths.AI_ARTIST_MD etc., not "from ... import".
_DATA_PATHS = {
    "AI_ARTIST_MD": paths.DATA_DIR / "ai_artist.md",
    "AI_ARCHIVE_TXT": paths.DATA_DIR / "ai_generated_posts_archive.txt",
    # The review GUIs write these; the original kept them in config/ (moved to data/ 2026-09-30)
    "KEPT_POSTS": paths.DATA_DIR / "kept_posts.txt",
    "KEPT_POSTS_ARTISTS": paths.DATA_DIR / "kept_posts_artists.txt",
    "MD5_SEARCH_DIR": paths.DATA_DIR / "md5_search",
    "REPORTS_DIR": paths.DATA_DIR / "archive" / "reports",
    "TEMP_DOWNLOADS": paths.CACHE_DIR / "archive" / "temp_downloads",
    "PAGE_CACHE": paths.E621_PAGE_CACHE,  # was temp_responses/
}


def __getattr__(name):
    if name in _DATA_PATHS:
        return paths.for_env(_DATA_PATHS[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# was ./data/logs for per-script logs
LOG_DIR = paths.LOG_DIR / "archive"

# Default folders can be overridden with archive.ai_cleanup.* settings.
DEFAULT_AI_TAG_DIRECTORY = r"E:\Image Sorter Working Folder\AI"
DEFAULT_AI_ARTIST_DIRECTORY = r"E:\Image Sorter Working Folder\AI\AI_Artist_Trash"


def _ai_cleanup() -> dict:
    try:
        return fff_config.legacy_view("archive").get("ai_cleanup") or {}
    except fff_config.ConfigError:
        return {}


def _test_path(path: str) -> str:
    """Reroute a fallback default the same way configured folders are (E:/Testing in test)."""
    try:
        return fff_config.test_path(path, fff_config.load())
    except fff_config.ConfigError:
        return path


def ai_tag_directory() -> str:
    """Where remove_ai_tagged_posts moves files."""
    return _ai_cleanup().get("ai_tag_directory") or _test_path(DEFAULT_AI_TAG_DIRECTORY)


def ai_artist_directory() -> str:
    """Where remove_ai_artist_posts moves files."""
    return _ai_cleanup().get("ai_artist_directory") or _test_path(DEFAULT_AI_ARTIST_DIRECTORY)


DEFAULT_UNFAVORITED_DIRECTORY = r"E:\Image Sorter Working Folder\Unfavorited"


def unfavorited_directory() -> str:
    """Where files of posts removed from favorites are moved (archive.e621.unfavorited_directory)."""
    try:
        configured = (fff_config.legacy_view("archive").get("e621") or {}).get("unfavorited_directory")
    except fff_config.ConfigError:
        configured = None
    return configured or _test_path(DEFAULT_UNFAVORITED_DIRECTORY)


def replaced_directory() -> str:
    """Where a post's old local file goes when e621 has replaced the post's file
    (archive.e621.replaced_directory). Defaults to a folder beside the unfavorited one."""
    try:
        configured = (fff_config.legacy_view("archive").get("e621") or {}).get("replaced_directory")
    except fff_config.ConfigError:
        configured = None
    return configured or str(Path(unfavorited_directory()).parent / "Replaced on e621")


def db_names() -> dict:
    """module -> schema for the active environment (prod or test_*)."""
    return fff_config.database_names(fff_config.load())


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
