"""Filename conventions shared by all modules.

Every function here is copied verbatim from its previous component; the source is
noted above each group. tests/test_windows_filenames.py checks them against the existing code.
"""
import os
import re
from typing import Any, Dict

# --- media sorter/_handlers/utils.py: file type and rating detection ---------

def is_configured_file(file):
    """Return True if the file extension is allowed."""
    allowed_extensions = {'.webm', '.mp4', '.mov', '.avi', '.gif', '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}
    ext = os.path.splitext(file)[1].lower()

    # Ignore system/metadata files
    if file.lower() in ('desktop.ini', 'thumbs.db', '.ds_store'):
        return False

    # Ignore text and documentation files
    if ext in ('.txt', '.md'):
        return False

    return ext in allowed_extensions


def is_video(file):
    """Check if file is a video."""
    return os.path.splitext(file)[1].lower() in ['.webm', '.mp4', '.mov', '.avi']


def is_gif(file):
    """Check if file is a GIF."""
    return os.path.splitext(file)[1].lower() == '.gif'


def is_image(file):
    """Check if file is an image."""
    return os.path.splitext(file)[1].lower() in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp']


def is_explicit(file):
    """Check if file is marked as explicit."""
    return 'explicit' in file.lower()


def is_adult(file):
    """Check if file is marked as adult (same as explicit)."""
    return 'adult' in file.lower()


def is_questionable(file):
    """Check if file is marked as questionable."""
    return 'questionable' in file.lower()


def is_mature(file):
    """Check if file is marked as mature (same as questionable)."""
    return 'mature' in file.lower()


def is_safe(file):
    """Check if file is marked as safe."""
    return 'safe' in file.lower()


def is_general(file):
    """Check if file is marked as general (same as safe)."""
    return 'general' in file.lower()


def is_explicit_or_adult(file):
    """Check if file is marked as explicit, adult, questionable, or mature (all treated as NSFW)."""
    return (is_explicit(file) or is_adult(file) or
            is_questionable(file) or is_mature(file))


def is_questionable_or_mature(file):
    """Check if file is marked as questionable or mature (now treated same as explicit)."""
    return is_questionable(file) or is_mature(file)


def is_sfw_or_general(file):
    """Check if file is marked as safe or general (both are SFW ratings)."""
    return is_safe(file) or is_general(file)


def is_furaffinity_file(file):
    """Check if file was downloaded from FurAffinity (identified by '-FA-' in the filename)."""
    return '-fa-' in file.lower()


def get_furaffinity_rating(file):
    """
    Determine the FurAffinity content rating from a filename.

    FurAffinity files use 'general', 'mature', or 'adult' to indicate rating
    (as opposed to e621's 'safe'/'questionable'/'explicit').

    Returns:
        'General', 'Mature', 'Adult', or None if no rating keyword is found.
    """
    filename_lower = file.lower()
    if 'adult' in filename_lower:
        return 'Adult'
    if 'mature' in filename_lower:
        return 'Mature'
    if 'general' in filename_lower:
        return 'General'
    return None


def extract_e621_post_id(filename):
    """
    Extract e621 post ID from filename if it matches the pattern.

    Pattern: filename must contain '-explicit-', '-questionable-', or '-safe-'
    (surrounded by dashes), and the post ID is the number after the last dash
    before the extension.

    Files with '-FA-' in the name are from FurAffinity, not e621, so return None.

    Examples:
        'artist-name-explicit-12345.jpg' -> 12345
        'character-safe-67890.png' -> 67890
        'something-questionable-11111.webp' -> 11111
        'artist-name-explicit-12345-1.jpg' -> 12345 (handles duplicate suffixes)
        'artist-name-FA-explicit-12345.jpg' -> None (FurAffinity file)
        'explicit-file.jpg' -> None (no dashes around 'explicit')
        'no-rating-12345.jpg' -> None (no rating keyword)
        'explicit-file.jpg' -> None (no number after dash)

    Args:
        filename: The filename to parse (with or without extension)

    Returns:
        int: The post ID if pattern matches, None otherwise
    """
    import re

    # Convert to lowercase for case-insensitive matching
    filename_lower = filename.lower()

    # If filename contains -FA-, it's from FurAffinity, not e621
    if '-fa-' in filename_lower:
        return None

    # Check if filename contains one of the rating keywords surrounded by dashes
    if not any(rating in filename_lower for rating in ['-explicit-', '-questionable-', '-safe-']):
        return None

    # Remove file extension
    name_without_ext = os.path.splitext(filename)[0]

    # Find the last dash and check if what follows is a number
    last_dash_idx = name_without_ext.rfind('-')
    if last_dash_idx == -1:
        return None

    # Extract everything after the last dash
    potential_id = name_without_ext[last_dash_idx + 1:].strip()

    # Check if it's a valid number
    if potential_id.isdigit():
        # Check if this is a single digit (likely a duplicate suffix like -1, -2)
        # If so, try to get the number before this
        if len(potential_id) <= 2:  # Single or double digit (e.g., -1, -2, -10)
            # Find the second-to-last dash
            second_last_dash_idx = name_without_ext[:last_dash_idx].rfind('-')
            if second_last_dash_idx != -1:
                # Extract the number between the two dashes
                potential_post_id = name_without_ext[second_last_dash_idx + 1:last_dash_idx].strip()
                # If this is a valid multi-digit number, use it instead
                if potential_post_id.isdigit() and len(potential_post_id) > 2:
                    return int(potential_post_id)
        # Otherwise use the original number
        return int(potential_id)

    return None


# --- e621 archive/modules/db.py: flag-token cleanup ----------------------

FLAG_TOKENS = [
    "conditional_dnp",
    "sound_warning",
    "third-party-edit",
    "third-party_edit",
]


def strip_flag_tokens(name: str) -> str:
    """Remove known flag tokens (e.g. 'conditional_dnp', 'sound_warning') from a name.

    Mirrors the cleanup logic in media sorter so filenames stay consistent.
    """
    if not name:
        return name

    new_name = name
    for token in FLAG_TOKENS:
        patterns = [
            f"_{token}",
            f"-{token}",
            f"{token}_",
            f"{token}-",
        ]
        for pattern in patterns:
            new_name = new_name.replace(pattern, "")

        # Fallback: strip any remaining bare token
        new_name = new_name.replace(token, "")

    # Clean up repeated separators and stray punctuation
    while "__" in new_name:
        new_name = new_name.replace("__", "_")
    while "--" in new_name:
        new_name = new_name.replace("--", "-")
    new_name = new_name.strip("-_ .")

    return new_name or name


# --- e621 archive/modules/download.py -----------------------------------

def _filename_has_exact_post_id(fname, post_id):
    """Return True when fname encodes post_id in the canonical e621 filename pattern."""
    base_name = os.path.splitext(os.path.basename(fname))[0]
    match = re.search(r"-(?:safe|questionable|explicit)-(\d+)(?:-\d+)?$", base_name, re.IGNORECASE)
    if not match:
        return False

    return match.group(1) == str(post_id)


# --- reverse-image search ---------------------------------------------------

def sanitize_filename_component(value: str) -> str:
    """Sanitize a string for safe filename usage."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', str(value).strip())
    sanitized = re.sub(r'\s+', '_', sanitized)
    return sanitized or 'unknown'


def generate_fa_filename(post_data: Dict[str, Any]) -> str:
    """Generate filename for FA download: {artist}-{rating}-FA-{postid}.{ext}"""
    # Safely get values with defaults
    artist = post_data.get('artist', 'unknown').replace('/', '_').replace('\\', '_')
    rating = post_data.get('rating', 'Unknown')
    post_id = post_data.get('post_id', 'unknown')
    ext = post_data.get('extension', '.jpg')

    # Sanitize filename - remove invalid characters
    safe_artist = re.sub(r'[<>:"|?*]', '_', artist)
    safe_artist = safe_artist.strip()

    return f"{safe_artist}-{rating}-FA-{post_id}{ext}"
