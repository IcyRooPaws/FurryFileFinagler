#!/usr/bin/env python3
# fff: implemented in media sorter/scripts/duplicate_manager_ui.py.
# fff: imports come from fff.sorter.handlers / fff.sorter.config; hashing from fff.hashing.
# fff: SQL schema qualifiers use the active environment's sorter schema (_sorter_db()); the
# fff: unqualified all_files view is left alone (the connection already targets that schema).
# fff: main(argv=None) ignores argv (the original had no options). File moves/deletes are unchanged.
"""
Duplicate Manager GUI - Interactive duplicate file management

This provides a graphical interface for:
- Finding duplicate files across all database tables
- Previewing duplicate groups with side-by-side comparison
- Creating hybrid files by mixing attributes (content, filename, location, date)
- Safe file operations with database updates
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import subprocess
import threading
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
from PIL import Image, ImageTk, ImageSequence
import tempfile
import hashlib

# fff: the sys.path hack is gone; the _handlers imports come from fff.sorter.handlers. The
# ImportError fallback to fff.sorter.core.database was dropped (that module was dropped, decision 4).
from fff.sorter.handlers import DatabaseManager
from fff.sorter.handlers.utils import Config
from fff.sorter import paths as sorter_paths

import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _sorter_db() -> str:
    """fff: the sorter schema for the active environment (prod or test_); the original
    qualified its tables with a hard-coded schema name."""
    return sorter_paths.db_names()["sorter"]


def _set_file_timestamps(file_path: str, atime: Optional[float] = None,
                         mtime: Optional[float] = None,
                         ctime: Optional[float] = None) -> None:
    """Apply access/modified times and, on Windows, creation time."""
    stat_result = os.stat(file_path)
    atime = stat_result.st_atime if atime is None else atime
    mtime = stat_result.st_mtime if mtime is None else mtime
    os.utime(file_path, (atime, mtime))

    if os.name != 'nt' or ctime is None:
        return

    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ('dwLowDateTime', wintypes.DWORD),
                ('dwHighDateTime', wintypes.DWORD),
            ]

        def to_filetime(timestamp: float) -> FILETIME:
            value = int(timestamp * 10000000) + 116444736000000000
            return FILETIME(value & 0xFFFFFFFF, value >> 32)

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE

        set_file_time = kernel32.SetFileTime
        set_file_time.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        set_file_time.restype = wintypes.BOOL

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            file_path,
            0x0100,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x80,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), f"CreateFileW failed for {file_path}")

        try:
            created = to_filetime(ctime)
            accessed = to_filetime(atime)
            modified = to_filetime(mtime)
            if not set_file_time(handle, ctypes.byref(created), ctypes.byref(accessed), ctypes.byref(modified)):
                raise OSError(ctypes.get_last_error(), f"SetFileTime failed for {file_path}")
        finally:
            close_handle(handle)
    except Exception as exc:
        logger.warning(f"Failed to set creation time for {file_path}: {exc}")


def hamming_distance(hash1: str, hash2: str, max_threshold: int = None) -> int:
    """
    Calculate the Hamming distance between two hash strings.
    Returns the number of differing characters.

    Supports composite hash formats:
    - hybrid: "dhash|phash" - averages distances of both components
    - multi: "avg|dhash|phash|whash" - averages distances of all components

    Args:
        hash1: First hash string (may be composite)
        hash2: Second hash string (may be composite)
        max_threshold: Early termination - return if distance exceeds this
    """
    if not hash1 or not hash2:
        return float('inf')  # Invalid comparison

    # Check if we have composite hashes (hybrid or multi mode)
    if '|' in hash1 and '|' in hash2:
        # Split into components
        components1 = hash1.split('|')
        components2 = hash2.split('|')

        # Must have same number of components
        if len(components1) != len(components2):
            return float('inf')

        # Calculate average distance across all components
        total_distance = 0
        for comp1, comp2 in zip(components1, components2):
            if len(comp1) != len(comp2):
                return float('inf')

            comp_distance = 0
            for c1, c2 in zip(comp1, comp2):
                if c1 != c2:
                    comp_distance += 1
            total_distance += comp_distance

        # Return average distance across components
        avg_distance = total_distance // len(components1)
        return avg_distance

    # Standard single hash comparison
    if len(hash1) != len(hash2):
        return float('inf')  # Invalid comparison

    distance = 0
    for c1, c2 in zip(hash1, hash2):
        if c1 != c2:
            distance += 1
            # Early termination if we've exceeded threshold
            if max_threshold is not None and distance > max_threshold:
                return distance
    return distance


def similarity_percentage(hash1: str, hash2: str, min_similarity: float = None) -> float:
    """
    Calculate similarity percentage between two hashes.
    Returns 0-100 where 100 is identical.

    Supports composite hash formats (hybrid, multi) by comparing components.

    Args:
        hash1: First hash string (may be composite)
        hash2: Second hash string (may be composite)
        min_similarity: Early termination - return 0 if below this threshold
    """
    if not hash1 or not hash2:
        return 0.0

    # Handle composite hashes
    if '|' in hash1 and '|' in hash2:
        components1 = hash1.split('|')
        components2 = hash2.split('|')

        if len(components1) != len(components2):
            return 0.0

        # Use first component length for max_distance calculation
        # (all components should be same length in a properly formatted hash)
        max_distance = len(components1[0]) if components1 else 0
    else:
        # Standard single hash
        if len(hash1) != len(hash2):
            return 0.0
        max_distance = len(hash1)

    # Calculate max allowed distance for early termination
    max_threshold = None
    if min_similarity is not None:
        max_threshold = int(max_distance * (1 - min_similarity / 100.0))

    distance = hamming_distance(hash1, hash2, max_threshold)

    # Early exit if threshold exceeded or invalid
    if distance == float('inf') or (max_threshold is not None and distance > max_threshold):
        return 0.0

    similarity = ((max_distance - distance) / max_distance) * 100
    return similarity


def get_file_type(file_path: str) -> str:
    """
    Determine the file type category from file path.

    Args:
        file_path: Path to the file

    Returns:
        'video', 'gif', or 'image'
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in ['.webm', '.mp4', '.mov', '.avi']:
        return 'video'
    elif ext == '.gif':
        return 'gif'
    elif ext in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp']:
        return 'image'
    else:
        # Default to image for unknown types
        return 'image'


# Cache audio probe results to avoid repeated ffprobe calls
_audio_probe_cache: Dict[str, Optional[bool]] = {}


def video_has_audio(file_path: str) -> Optional[bool]:
    """Return True if video has an audio stream, False if not, None if unknown.

    Uses ffprobe when available; caches results to avoid repeated subprocess calls.
    """
    if not file_path or not os.path.exists(file_path):
        return None

    if file_path in _audio_probe_cache:
        return _audio_probe_cache[file_path]

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        file_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        stdout = (result.stdout or "").strip().lower()
        has_audio = bool(stdout)
        _audio_probe_cache[file_path] = has_audio
        return has_audio
    except FileNotFoundError:
        # ffprobe not available
        _audio_probe_cache[file_path] = None
        return None
    except Exception as e:
        logger.debug(f"ffprobe audio check failed for {file_path}: {e}")
        _audio_probe_cache[file_path] = None
        return None


class DuplicateGroup:
    """Represents a group of duplicate files with the same hash."""

    def __init__(self, hash_value: str, files: List[Dict], config=None):
        self.hash_value = hash_value
        self.files = files
        self.config = config
        # Note: Old selection tracking attributes removed (selected_to_keep, files_to_move, file_to_replace_keep)
        # New immediate-action model doesn't require selection state

    def get_priority_sorted_files(self) -> List[Dict]:
        """Sort files by priority (directory, filename patterns, -FA- tag, table priority).

        Uses configurable patterns for both directory and filename prioritization.
        """
        priority_order = {
            'archive_premium': 0,
            'favorites': 1,
            'manual_sort_images': 2,
            'manual_sort_videos': 2,
            'sfw_image': 3,
            'sfw_video': 3,
            'sfw_gif': 3,
            'nsfw_image': 4,
            'nsfw_video': 4,
            'nsfw_gif': 4,
            'archive_explicit_images': 5,
            'archive_explicit_videos': 5,
            'archive_explicit_gifs': 5,
            'archive_sfw_images': 5,
            'archive_sfw_videos': 5,
            'archive_sfw_gifs': 5
        }

        def get_sort_key(f):
            filename = os.path.basename(f['file_path']).lower()
            file_path_lower = f['file_path'].lower()

            # Priority 0: Directory path priority from config
            dir_priority = self._get_directory_priority(file_path_lower)

            # Priority 1: Filename pattern priority from config
            filename_priority = self._get_filename_priority(filename)

            # Priority 2: Has -FA- artist tag
            has_fa_tag = '-fa-' in filename
            fa_priority = 0 if has_fa_tag else 1

            # Priority 3: Table priority
            table_priority = priority_order.get(f['table_name'], 99)

            return (
                dir_priority,         # Directory priority from config (0 = highest)
                filename_priority,    # Filename pattern priority from config
                fa_priority,          # Then -FA- tagged files
                table_priority,       # Then by table priority
                f.get('file_modified_at', ''),
                f['file_path']
            )

        return sorted(self.files, key=get_sort_key)

    def _get_directory_priority(self, file_path_lower: str) -> int:
        """Get priority penalty for a file based on its directory path.

        Args:
            file_path_lower: Lowercase file path

        Returns:
            Priority number (0 = highest priority, higher numbers = lower priority)
        """
        # If no config is available, use default behavior
        if not hasattr(self, 'config') or self.config is None:
            # Default: penalize 'unsorted' directories
            return 90 if 'unsorted' in file_path_lower else 0

        priorities: List[Dict] = []

        # Try new configuration interface first
        try:
            from fff.sorter.config import Config as NewConfig  # fff: was fff.sorter.config
            if isinstance(self.config, NewConfig):
                priorities = self.config.get_directory_priorities()
        except Exception:
            priorities = []

        # Fallback to legacy configuration data structure
        if not priorities:
            root_config = getattr(self.config, 'config', None)
            if isinstance(root_config, dict):
                duplicate_cfg = root_config.get('duplicate_manager', {})
                if isinstance(duplicate_cfg, dict):
                    priorities = duplicate_cfg.get('directory_priority', [])

        # If no priorities configured, use default behavior
        if not priorities:
            return 90 if 'unsorted' in file_path_lower else 0

        # Find the highest priority (lowest number) that matches
        matched_priority = None
        for entry in priorities:
            pattern = entry.get('pattern', '').lower()
            priority = entry.get('priority', 50)

            if pattern and pattern in file_path_lower:
                if matched_priority is None or priority < matched_priority:
                    matched_priority = priority

        # Return matched priority or default (50 = medium priority)
        return matched_priority if matched_priority is not None else 50

    def _get_filename_priority(self, filename_lower: str) -> int:
        """Get priority penalty for a file based on its filename patterns.

        Args:
            filename_lower: Lowercase filename

        Returns:
            Priority number (0 = highest priority, higher numbers = lower priority)
        """
        # If no config is available, use default behavior
        if not hasattr(self, 'config') or self.config is None:
            # Default: prefer explicit/questionable/safe over general/mature/adult
            if any(rating in filename_lower for rating in ['explicit', 'questionable', 'safe']):
                return 0
            elif any(rating in filename_lower for rating in ['general', 'mature', 'adult']):
                return 50
            return 75  # No rating found - lowest priority

        priorities: List[Dict] = []

        # Try new configuration interface first
        try:
            from fff.sorter.config import Config as NewConfig  # fff: was fff.sorter.config
            if isinstance(self.config, NewConfig):
                priorities = self.config.get_filename_priorities()
        except Exception:
            priorities = []

        # Fallback to legacy configuration data structure
        if not priorities:
            root_config = getattr(self.config, 'config', None)
            if isinstance(root_config, dict):
                duplicate_cfg = root_config.get('duplicate_manager', {})
                if isinstance(duplicate_cfg, dict):
                    priorities = duplicate_cfg.get('filename_priority', [])

        # If no priorities configured, use default behavior
        if not priorities:
            if any(rating in filename_lower for rating in ['explicit', 'questionable', 'safe']):
                return 0
            elif any(rating in filename_lower for rating in ['general', 'mature', 'adult']):
                return 50
            return 75  # No rating found - lowest priority

        # Find the highest priority (lowest number) that matches
        matched_priority = None
        for entry in priorities:
            pattern = entry.get('pattern', '').lower()
            priority = entry.get('priority', 50)

            if pattern and pattern in filename_lower:
                if matched_priority is None or priority < matched_priority:
                    matched_priority = priority

        # Return matched priority or default (75 = low priority for unrecognized filenames)
        # This ensures that files with ANY recognized rating tag are preferred over
        # generic filenames with no rating information
        return matched_priority if matched_priority is not None else 75


class ImagePreviewCache:
    """Handles image preview generation and caching, including animated GIFs and videos."""

    def __init__(self, max_cache_size=50):
        self.cache = {}
        self.max_cache_size = max_cache_size
        self.preview_size = (350, 350)  # Increased from 200x200 for better visibility
        self.gif_frames_cache = {}  # Store GIF animation frames
        self.video_frames_cache = {}  # Store video frames

    def get_preview(self, file_path: str, max_size: tuple = None) -> Optional[ImageTk.PhotoImage]:
        """Get or generate preview image for file.

        Args:
            file_path: Path to the file
            max_size: Optional (width, height) tuple for custom preview size
        """
        # Use custom size or default
        preview_size = max_size if max_size else self.preview_size

        # Create cache key with size
        cache_key = f"{file_path}_{preview_size[0]}x{preview_size[1]}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        try:
            # Check if file exists
            if not os.path.exists(file_path):
                return None

            # Determine file type and generate preview
            ext = os.path.splitext(file_path)[1].lower()

            if ext == '.gif':
                preview = self._generate_gif_preview(file_path, preview_size)
            elif ext in ['.jpg', '.jpeg', '.png', '.bmp', '.webp']:
                preview = self._generate_image_preview(file_path, preview_size)
            elif ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']:
                preview = self._generate_video_preview(file_path, preview_size)
            else:
                preview = self._generate_placeholder_preview(ext, preview_size)

            # Add to cache (with size management)
            if len(self.cache) >= self.max_cache_size:
                # Remove oldest entries
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]

            self.cache[cache_key] = preview
            return preview

        except Exception as e:
            logger.warning(f"Failed to generate preview for {file_path}: {e}")
            return self._generate_placeholder_preview("error", preview_size)

    def get_gif_frames(self, file_path: str, max_size: tuple = None) -> Optional[List[Tuple[ImageTk.PhotoImage, int]]]:
        """Get all frames and durations for animated GIF.

        Returns:
            List of (PhotoImage, duration_ms) tuples, or None if not a GIF
        """
        preview_size = max_size if max_size else self.preview_size
        cache_key = f"{file_path}_frames_{preview_size[0]}x{preview_size[1]}"

        if cache_key in self.gif_frames_cache:
            return self.gif_frames_cache[cache_key]

        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext != '.gif':
                return None

            frames = []
            with Image.open(file_path) as img:
                for frame in ImageSequence.Iterator(img):
                    # Get frame duration (default to 100ms if not specified)
                    duration = frame.info.get('duration', 100)

                    # Convert to RGB
                    frame_rgb = frame.convert('RGB')

                    # Resize
                    frame_rgb.thumbnail(preview_size, Image.Resampling.LANCZOS)

                    # Create PhotoImage
                    photo = ImageTk.PhotoImage(frame_rgb)
                    frames.append((photo, duration))

            if frames:
                self.gif_frames_cache[cache_key] = frames
                return frames

            return None

        except Exception as e:
            logger.warning(f"Failed to extract GIF frames from {file_path}: {e}")
            return None

    def _generate_image_preview(self, file_path: str, preview_size: tuple = None) -> ImageTk.PhotoImage:
        """Generate preview for image files."""
        if preview_size is None:
            preview_size = self.preview_size

        with Image.open(file_path) as img:
            # Convert to RGB if necessary
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')

            # Create thumbnail
            img.thumbnail(preview_size, Image.Resampling.LANCZOS)

            # Create PhotoImage
            return ImageTk.PhotoImage(img)

    def _generate_gif_preview(self, file_path: str, preview_size: tuple = None) -> ImageTk.PhotoImage:
        """Generate preview for GIF files (first frame)."""
        if preview_size is None:
            preview_size = self.preview_size

        with Image.open(file_path) as img:
            # Get first frame
            img.seek(0)

            # Convert to RGB
            frame = img.convert('RGB')

            # Create thumbnail
            frame.thumbnail(preview_size, Image.Resampling.LANCZOS)

            # Create PhotoImage
            return ImageTk.PhotoImage(frame)
        try:
            # Use optional opencv if available (module-level cv2 set at import time)
            if cv2 is None:
                # OpenCV not available, use placeholder
                return self._generate_placeholder_preview("video", preview_size)

            # Try to extract first frame with opencv
            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            cap.release()

            if ret and frame is not None:
                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # Convert to PIL Image
                img = Image.fromarray(frame_rgb)

                # Create thumbnail
                img.thumbnail(preview_size, Image.Resampling.LANCZOS)

                return ImageTk.PhotoImage(img)
            else:
                return self._generate_placeholder_preview("video", preview_size)

        except ImportError:
            # OpenCV not available, use placeholder
            return self._generate_placeholder_preview("video", preview_size)
        except Exception as e:
            logger.warning(f"Failed to extract video frame from {file_path}: {e}")
            return self._generate_placeholder_preview("video", preview_size)

    def _generate_placeholder_preview(self, file_type: str, preview_size: tuple = None) -> ImageTk.PhotoImage:
        """Generate placeholder preview for unsupported files."""
        if preview_size is None:
            preview_size = self.preview_size

        img = Image.new('RGB', preview_size, color='lightgray')

        # You could add text or icons here
        return ImageTk.PhotoImage(img)

    def clear_cache(self):
        """Clear the preview cache."""
        self.cache.clear()
        self.gif_frames_cache.clear()
        self.video_frames_cache.clear()


class AnimatedGifWidget(ttk.Frame):
    """Widget that displays an animated GIF."""

    def __init__(self, parent, file_path: str, preview_cache: ImagePreviewCache, max_size: tuple = None):
        super().__init__(parent)
        self.file_path = file_path
        self.preview_cache = preview_cache
        self.max_size = max_size or (300, 300)
        self.frames = None
        self.current_frame = 0
        self.animation_id = None
        self.is_playing = True

        # Create label to hold the image
        self.label = ttk.Label(self)
        self.label.pack()

        # Load frames
        self.load_frames()

        # Start animation
        if self.frames and len(self.frames) > 1:
            self.animate()

    def load_frames(self):
        """Load all frames from the GIF."""
        self.frames = self.preview_cache.get_gif_frames(self.file_path, self.max_size)
        if self.frames and len(self.frames) > 0:
            self.label.configure(image=self.frames[0][0])

    def animate(self):
        """Animate the GIF by cycling through frames."""
        if not self.is_playing or not self.frames or len(self.frames) <= 1:
            return

        # Display current frame
        photo, duration = self.frames[self.current_frame]
        self.label.configure(image=photo)

        # Move to next frame
        self.current_frame = (self.current_frame + 1) % len(self.frames)

        # Schedule next frame
        self.animation_id = self.after(duration, self.animate)

    def stop(self):
        """Stop the animation."""
        self.is_playing = False
        if self.animation_id:
            self.after_cancel(self.animation_id)
            self.animation_id = None

    def destroy(self):
        """Clean up when widget is destroyed."""
        self.stop()
        super().destroy()


class VideoPlayerWidget(ttk.Frame):
    """Widget that displays a video with play/pause controls."""

    # Class variable to track currently playing video
    _current_playing_video = None

    def __init__(self, parent, file_path: str, preview_cache: ImagePreviewCache, max_size: tuple = None):
        super().__init__(parent)
        self.file_path = file_path
        self.preview_cache = preview_cache
        self.max_size = max_size or (300, 300)
        self.is_playing = False
        self.video_cap = None
        self.animation_id = None
        self.fps = 30  # Default FPS

        # Create UI elements
        self.create_ui()

        # Load initial frame (thumbnail)
        self.load_thumbnail()

    def create_ui(self):
        """Create the video player UI."""
        # Video display label
        self.video_label = ttk.Label(self, cursor="hand2")
        self.video_label.pack()

        # Play/pause button
        button_frame = ttk.Frame(self)
        button_frame.pack(pady=(5, 0))

        self.play_button = ttk.Button(button_frame, text="> Play",
                                      command=self.toggle_play, width=10)
        self.play_button.pack(side='left', padx=2)

        self.stop_button = ttk.Button(button_frame, text="[STOP] Stop",
                                      command=self.stop_video, width=10,
                                      state='disabled')
        self.stop_button.pack(side='left', padx=2)

        # Click on video to play/pause
        self.video_label.bind('<Button-1>', lambda e: self.toggle_play())

    def load_thumbnail(self):
        """Load the thumbnail preview."""
        preview = self.preview_cache.get_preview(self.file_path, self.max_size)
        if preview:
            self.video_label.configure(image=preview)
            self.video_label.image = preview  # Keep reference

    def toggle_play(self):
        """Toggle play/pause state."""
        if self.is_playing:
            self.pause_video()
        else:
            self.play_video()

    def play_video(self):
        """Start playing the video."""
        # Stop any other playing video
        if VideoPlayerWidget._current_playing_video and VideoPlayerWidget._current_playing_video != self:
            VideoPlayerWidget._current_playing_video.stop_video()

        VideoPlayerWidget._current_playing_video = self

        try:
            import cv2

            if not self.video_cap:
                self.video_cap = cv2.VideoCapture(self.file_path)
                self.fps = self.video_cap.get(cv2.CAP_PROP_FPS) or 30

            self.is_playing = True
            self.play_button.configure(text="[PAUSE] Pause")
            self.stop_button.configure(state='normal')

            # Start playing frames
            self.play_next_frame()

        except ImportError:
            messagebox.showwarning("OpenCV Required",
                                  "Video playback requires opencv-python.\n"
                                  "Install with: pip install opencv-python")
        except Exception as e:
            logger.error(f"Failed to play video {self.file_path}: {e}")
            messagebox.showerror("Video Error", f"Failed to play video:\n{e}")

    def play_next_frame(self):
        """Play the next frame of the video."""
        if not self.is_playing or not self.video_cap:
            return

        try:
            import cv2

            ret, frame = self.video_cap.read()

            if ret:
                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # Resize to fit preview size
                height, width = frame_rgb.shape[:2]
                max_width, max_height = self.max_size

                # Calculate scaling
                scale = min(max_width / width, max_height / height)
                new_width = int(width * scale)
                new_height = int(height * scale)

                frame_resized = cv2.resize(frame_rgb, (new_width, new_height))

                # Convert to PIL Image
                img = Image.fromarray(frame_resized)
                photo = ImageTk.PhotoImage(img)

                # Update display
                self.video_label.configure(image=photo)
                self.video_label.image = photo  # Keep reference

                # Schedule next frame
                frame_delay = int(1000 / self.fps)
                self.animation_id = self.after(frame_delay, self.play_next_frame)
            else:
                # End of video, loop back to start
                self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.play_next_frame()

        except Exception as e:
            logger.error(f"Error playing video frame: {e}")
            self.stop_video()

    def pause_video(self):
        """Pause the video."""
        self.is_playing = False
        self.play_button.configure(text="> Play")
        if self.animation_id:
            self.after_cancel(self.animation_id)
            self.animation_id = None

    def stop_video(self):
        """Stop the video and reset to beginning."""
        self.is_playing = False
        self.play_button.configure(text="> Play")
        self.stop_button.configure(state='disabled')

        if self.animation_id:
            self.after_cancel(self.animation_id)
            self.animation_id = None

        if self.video_cap:
            self.video_cap.release()
            self.video_cap = None

        # Reset to thumbnail
        self.load_thumbnail()

        # Clear current playing reference if it's us
        if VideoPlayerWidget._current_playing_video == self:
            VideoPlayerWidget._current_playing_video = None

    def destroy(self):
        """Clean up when widget is destroyed."""
        self.stop_video()
        super().destroy()


class DuplicateManagerGUI:
    """Main GUI application for duplicate management."""

    def __init__(self, root, hash_column='perceptual_hash_phash'):
        self.root = root
        self.root.title(f"media sorter - Duplicate Manager ({hash_column})")
        self.root.geometry("1400x900")
        self.root.minsize(1200, 700)

        # Hash column configuration - now using separate perceptual hash columns
        self.hash_column = hash_column
        self.hash_column_display = {
            'perceptual_hash_dhash': 'Difference Hash (dhash)',
            'perceptual_hash_phash': 'Perceptual Hash (phash)',
            'perceptual_hash_average': 'Average Hash',
            'perceptual_hash_whash': 'Wavelet Hash (whash)',
            'md5_hash': 'MD5 Hash',
            'file_name': 'File Name'
        }.get(hash_column, hash_column)

        # Load configuration
        self.config = None
        self.db_manager = None
        self.ignore_archive_premium = False
        self.load_config()

        # Data
        self.duplicate_groups = []
        self.current_group_index = 0
        self.preview_cache = ImagePreviewCache()
        self.operation_log = []
        self.is_scanning = False  # Track if scan is in progress

        # Variables
        self.scan_progress = tk.DoubleVar()
        self.total_duplicates = tk.IntVar()
        self.total_files = tk.IntVar()
        self.current_group_var = tk.StringVar()
        self.status_text = tk.StringVar(value="Ready")
        self.skip_confirmations = tk.BooleanVar(value=False)  # Skip confirmation dialogs
        self.incremental_load = tk.BooleanVar(value=False)  # Load groups incrementally vs all at once
        self.skip_videos = tk.BooleanVar(value=True)  # Skip video files in duplicate search
        self.compact_layout = tk.BooleanVar(value=True)  # Prefer compact layout for previews
        self.compact_layout.trace_add('write', lambda *_: self._on_layout_toggle())

        # Create GUI
        self.create_widgets()
        self.bind_events()

        # Initial state
        self.update_navigation_state()

        # Show/hide Auto Handle All button based on initial hash column
        if self.hash_column.startswith('view_'):
            self.auto_handle_all_button.pack(fill='x', pady=(2, 2))

    def load_config(self):
        """Load media sorter configuration."""
        try:
            self.config = Config()
            self.db_manager = DatabaseManager(self.config)
            self.ignore_archive_premium = self._config_ignore_archive_premium()
            logger.info("Configuration loaded successfully")
            # Ensure checkpoint and session tables exist
            self._ensure_checkpoint_table()
            self._ensure_session_table()
            self._ensure_non_duplicate_groups_table()
        except Exception as e:
            messagebox.showerror("Configuration Error",
                               f"Failed to load configuration:\n{e}")

    def _config_ignore_archive_premium(self) -> bool:
        """Determine whether archive_premium should be ignored."""
        if hasattr(self.config, 'should_ignore_archive_premium'):
            try:
                return bool(self.config.should_ignore_archive_premium())
            except Exception:
                return False
        return bool(getattr(self.config, 'ignore_archive_premium', False))

    def _should_skip_table(self, table_name: str) -> bool:
        """Return True if table should be skipped from processing."""
        return self.ignore_archive_premium and table_name == 'archive_premium'

    def _ensure_checkpoint_table(self):
        """Create checkpoint table if it doesn't exist."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS `{_sorter_db()}`.`similarity_checkpoint` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `session_id` VARCHAR(64) NOT NULL,
                `last_index` INT NOT NULL,
                `threshold` INT NOT NULL,
                `total_files` INT NOT NULL,
                `similar_pairs` LONGTEXT NOT NULL,
                `processed_files` LONGTEXT NOT NULL,
                `comparisons_made` BIGINT NOT NULL DEFAULT 0,
                `comparisons_skipped` BIGINT NOT NULL DEFAULT 0,
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX `idx_session` (`session_id`),
                INDEX `idx_updated` (`updated_at`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """

            cursor.execute(create_table_query)
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("Checkpoint table verified/created")
        except Exception as e:
            logger.error(f"Failed to create checkpoint table: {e}")

    def _ensure_non_duplicate_groups_table(self):
        """Create the persistent list of groups that are known not to be duplicates."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()
            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS `{_sorter_db()}`.`non_duplicate_groups` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `hash_column` VARCHAR(128) NOT NULL,
                `group_signature` CHAR(64) NOT NULL,
                `file_count` INT NOT NULL,
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY `uq_non_duplicate_group` (`hash_column`, `group_signature`),
                INDEX `idx_non_duplicate_hash_column` (`hash_column`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
            cursor.execute(create_table_query)
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("Non-duplicate groups table verified/created")
        except Exception as e:
            logger.error(f"Failed to create non-duplicate groups table: {e}")

    def _group_signature(self, files: List[Dict]) -> str:
        """Return a stable signature for the files currently in a group."""
        paths = sorted(
            os.path.normcase(file_info['file_path'].replace('\\', '/'))
            for file_info in files
        )
        signature_input = f"{self.hash_column}\n" + "\n".join(paths)
        return hashlib.sha256(signature_input.encode('utf-8')).hexdigest()

    def _is_non_duplicate_group(self, files: List[Dict]) -> bool:
        """Check whether this exact group was previously marked non-duplicate."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT 1
                FROM `{_sorter_db()}`.`non_duplicate_groups`
                WHERE hash_column = %s AND group_signature = %s
                LIMIT 1
                """,
                (self.hash_column, self._group_signature(files)),
            )
            marked = cursor.fetchone() is not None
            cursor.close()
            conn.close()
            return marked
        except Exception as e:
            logger.error(f"Failed to check non-duplicate group: {e}")
            return False

    def mark_current_group_not_duplicate(self):
        """Persist the current group as intentionally not duplicate and remove it."""
        if not self.duplicate_groups or self.current_group_index >= len(self.duplicate_groups):
            return

        group = self.duplicate_groups[self.current_group_index]
        if not self.skip_confirmations.get() and not messagebox.askyesno(
            "Mark Not Duplicate",
            "This group will be hidden from future scans until its file membership changes. Continue?",
        ):
            return

        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT IGNORE INTO `{_sorter_db()}`.`non_duplicate_groups`
                    (hash_column, group_signature, file_count)
                VALUES (%s, %s, %s)
                """,
                (self.hash_column, self._group_signature(group.files), len(group.files)),
            )
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to mark group as non-duplicate: {e}")
            messagebox.showerror("Database Error", f"Could not save this group:\n{e}")
            return

        self.duplicate_groups.pop(self.current_group_index)
        if self.current_group_index >= len(self.duplicate_groups):
            self.current_group_index = max(0, len(self.duplicate_groups) - 1)
        self.total_duplicates.set(len(self.duplicate_groups))
        self.total_files.set(sum(len(item.files) for item in self.duplicate_groups))
        self.log_message("Marked group as not duplicate; it will be skipped in future scans")
        if self.duplicate_groups:
            self.display_current_group()
        else:
            self.status_text.set("No duplicate groups remaining")
            for widget in self.scrollable_frame.winfo_children():
                widget.destroy()
        self.update_navigation_state()

    def _save_checkpoint(self, session_id: str, last_index: int, threshold: int,
                        total_files: int, similar_pairs: List, processed_files: set,
                        comparisons_made: int, comparisons_skipped: int):
        """Save current scan progress to database."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            # Convert data to JSON for storage
            similar_pairs_json = json.dumps([
                {
                    'file1': {'table_name': p[0]['table_name'], 'hash_value': p[0]['hash_value'],
                             'file_path': p[0]['file_path'], 'file_modified_at': str(p[0].get('file_modified_at', '')),
                             'file_size': p[0].get('file_size', 0)},
                    'file2': {'table_name': p[1]['table_name'], 'hash_value': p[1]['hash_value'],
                             'file_path': p[1]['file_path'], 'file_modified_at': str(p[1].get('file_modified_at', '')),
                             'file_size': p[1].get('file_size', 0)},
                    'similarity': p[2]
                }
                for p in similar_pairs
            ])

            processed_files_json = json.dumps(list(processed_files))

            # Insert or update checkpoint
            query = f"""
            INSERT INTO `{_sorter_db()}`.`similarity_checkpoint`
            (session_id, last_index, threshold, total_files, similar_pairs, processed_files,
             comparisons_made, comparisons_skipped)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                last_index = VALUES(last_index),
                similar_pairs = VALUES(similar_pairs),
                processed_files = VALUES(processed_files),
                comparisons_made = VALUES(comparisons_made),
                comparisons_skipped = VALUES(comparisons_skipped),
                updated_at = CURRENT_TIMESTAMP
            """

            cursor.execute(query, (session_id, last_index, threshold, total_files,
                                  similar_pairs_json, processed_files_json,
                                  comparisons_made, comparisons_skipped))
            conn.commit()
            cursor.close()
            conn.close()
            logger.debug(f"Checkpoint saved at index {last_index}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")

    def _load_checkpoint(self, session_id: str) -> Optional[Dict]:
        """Load saved checkpoint from database."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor(dictionary=True)

            query = f"""
            SELECT * FROM `{_sorter_db()}`.`similarity_checkpoint`
            WHERE session_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """

            cursor.execute(query, (session_id,))
            checkpoint = cursor.fetchone()
            cursor.close()
            conn.close()

            if checkpoint:
                # Parse JSON fields
                checkpoint['similar_pairs'] = json.loads(checkpoint['similar_pairs'])
                checkpoint['processed_files'] = set(json.loads(checkpoint['processed_files']))
                logger.info(f"Loaded checkpoint from index {checkpoint['last_index']}")
                return checkpoint

            return None
        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}")
            return None

    def _clear_checkpoint(self, session_id: str):
        """Clear checkpoint after successful completion."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            query = f"DELETE FROM `{_sorter_db()}`.`similarity_checkpoint` WHERE session_id = %s"
            cursor.execute(query, (session_id,))
            conn.commit()
            cursor.close()
            conn.close()
            logger.info(f"Checkpoint cleared for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to clear checkpoint: {e}")

    def _cleanup_old_checkpoints(self, days: int = 7):
        """Remove checkpoints older than specified days."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            query = f"""
            DELETE FROM `{_sorter_db()}`.`similarity_checkpoint`
            WHERE updated_at < DATE_SUB(NOW(), INTERVAL %s DAY)
            """

            cursor.execute(query, (days,))
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()

            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old checkpoints")
        except Exception as e:
            logger.error(f"Failed to cleanup old checkpoints: {e}")

    def _ensure_session_table(self):
        """Create session save table if it doesn't exist."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS `{_sorter_db()}`.`duplicate_sessions` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `session_name` VARCHAR(255) NOT NULL UNIQUE,
                `duplicate_groups` LONGTEXT NOT NULL,
                `current_index` INT NOT NULL DEFAULT 0,
                `hash_type` VARCHAR(50) NOT NULL,
                `threshold` INT NOT NULL,
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX `idx_name` (`session_name`),
                INDEX `idx_updated` (`updated_at`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """

            cursor.execute(create_table_query)
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("Session table verified/created")
        except Exception as e:
            logger.error(f"Failed to create session table: {e}")

    def save_current_session(self):
        """Save the current duplicate groups and state to database."""
        if not self.duplicate_groups:
            messagebox.showwarning("No Session", "No duplicate groups to save.")
            return

        # Ensure table exists
        self._ensure_session_table()

        # Ask for session name
        from tkinter import simpledialog
        session_name = simpledialog.askstring(
            "Save Session",
            "Enter a name for this session:",
            parent=self.root
        )

        if not session_name:
            return

        try:
            # Serialize duplicate groups
            groups_data = []
            for group in self.duplicate_groups:
                groups_data.append({
                    'hash_value': group.hash_value,
                    'files': group.files
                })

            groups_json = json.dumps(groups_data)

            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            # Insert or update session
            query = f"""
            INSERT INTO `{_sorter_db()}`.`duplicate_sessions`
            (session_name, duplicate_groups, current_index, hash_type, threshold)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                duplicate_groups = VALUES(duplicate_groups),
                current_index = VALUES(current_index),
                hash_type = VALUES(hash_type),
                threshold = VALUES(threshold),
                updated_at = CURRENT_TIMESTAMP
            """

            cursor.execute(query, (
                session_name,
                groups_json,
                self.current_group_index,
                self.hash_type_var.get(),
                self.threshold_var.get()
            ))

            conn.commit()
            cursor.close()
            conn.close()

            self.log_message(f"[OK] Session saved: {session_name}")
            messagebox.showinfo("Session Saved", f"Session '{session_name}' saved successfully!")

        except Exception as e:
            logger.error(f"Failed to save session: {e}")
            messagebox.showerror("Save Failed", f"Failed to save session: {e}")

    def load_saved_session(self):
        """Load a previously saved session from database."""
        # Ensure table exists
        self._ensure_session_table()

        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # Get list of available sessions
            query = f"""
            SELECT session_name, created_at, updated_at,
                   JSON_LENGTH(duplicate_groups) as group_count
            FROM `{_sorter_db()}`.`duplicate_sessions`
            ORDER BY updated_at DESC
            """

            cursor.execute(query)
            sessions = cursor.fetchall()
            cursor.close()
            conn.close()

            if not sessions:
                messagebox.showinfo("No Sessions", "No saved sessions found.")
                return

            # Create selection dialog
            from tkinter import simpledialog
            session_list = "\n".join([
                f"{i+1}. {s['session_name']} ({s['group_count']} groups) - {s['updated_at']}"
                for i, s in enumerate(sessions)
            ])

            selection = simpledialog.askstring(
                "Load Session",
                f"Available sessions:\n\n{session_list}\n\nEnter session name to load:",
                parent=self.root
            )

            if not selection:
                return

            # Load the selected session
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor(dictionary=True)

            query = f"""
            SELECT * FROM `{_sorter_db()}`.`duplicate_sessions`
            WHERE session_name = %s
            """

            cursor.execute(query, (selection,))
            session_data = cursor.fetchone()
            cursor.close()
            conn.close()

            if not session_data:
                messagebox.showerror("Not Found", f"Session '{selection}' not found.")
                return

            # Deserialize and restore state
            groups_data = json.loads(session_data['duplicate_groups'])

            self.duplicate_groups = []
            for group_data in groups_data:
                group = DuplicateGroup(
                    hash_value=group_data['hash_value'],
                    files=group_data['files'],
                    config=self.config
                )
                self.duplicate_groups.append(group)

            self.current_group_index = session_data['current_index']
            self.hash_type_var.set(session_data['hash_type'])
            self.threshold_var.set(session_data['threshold'])

            # Update UI
            self.total_duplicates.set(len(self.duplicate_groups))
            total_files = sum(len(g.files) for g in self.duplicate_groups)
            self.status_text.set(f"Loaded session: {selection} ({len(self.duplicate_groups)} groups, {total_files} files)")

            # Display current group
            self.display_current_group()

            self.log_message(f"[OK] Session loaded: {selection}")
            messagebox.showinfo("Session Loaded", f"Session '{selection}' loaded successfully!")

        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            messagebox.showerror("Load Failed", f"Failed to load session: {e}")

    def create_widgets(self):
        """Create the main GUI layout."""
        # Create main paned window
        main_paned = ttk.PanedWindow(self.root, orient='horizontal')
        main_paned.pack(fill='both', expand=True, padx=5, pady=5)

        # Left panel - controls and navigation (narrower)
        left_frame = ttk.Frame(main_paned, width=300)
        main_paned.add(left_frame, weight=0)

        # Right panel - preview area (gets most space)
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=1)

        self.create_control_panel(left_frame)
        self.create_preview_panel(right_frame)

    def create_control_panel(self, parent):
        """Create the control panel on the left side."""
        # ===== STEP 1: Detection Method =====
        scan_frame = ttk.LabelFrame(parent, text="1⃣ Detection", padding=2)
        scan_frame.pack(fill='x', pady=(0, 2))

        # Detection mode selector (shortened labels)
        self.hash_type_var = tk.StringVar(value=self.hash_column)
        hash_types = [
            ('perceptual_hash_phash', '[TARGET] Perceptual (phash)'),
            ('perceptual_hash_dhash', '[Loop] Difference (dhash)'),
            ('perceptual_hash_average', '[FAST] Average'),
            ('perceptual_hash_whash', '[STREAM] Wavelet (whash)'),
            ('perceptual_hash_similar', '[SEARCH] Similar (Multi-algo)'),
            ('view_perceptual_hash_phash', '[TARGET] View: Perceptual (phash)'),
            ('view_perceptual_hash_dhash', '[Loop] View: Difference (dhash)'),
            ('view_perceptual_hash_average', '[FAST] View: Average'),
            ('view_perceptual_hash_whash', '[STREAM] View: Wavelet (whash)'),
            ('md5_hash', '[SECURE] Binary (Exact)'),
            ('file_name', '[Notes] Name Match'),
            ('e621_post_id', '[NUMBER] e621 Post ID')
        ]

        # Store mapping for display
        self.hash_type_mapping = {ht[0]: ht[1] for ht in hash_types}
        self.hash_type_reverse_mapping = {ht[1]: ht[0] for ht in hash_types}

        hash_combo = ttk.Combobox(
            scan_frame,
            textvariable=self.hash_type_var,
            values=[ht[1] for ht in hash_types],
            state='readonly',
            width=22,
            font=('TkDefaultFont', 9)
        )
        hash_combo.set(self.hash_type_mapping.get(self.hash_column, hash_types[0][1]))
        hash_combo.pack(fill='x', pady=(0, 2))
        hash_combo.bind('<<ComboboxSelected>>', self.on_hash_type_changed)
        self.hash_type_combo = hash_combo

        # Similarity threshold (only for perceptual hash similar mode)
        self.similarity_frame = ttk.Frame(scan_frame)

        threshold_control = ttk.Frame(self.similarity_frame)
        threshold_control.pack(fill='x')

        ttk.Label(threshold_control, text="Min:", font=('TkDefaultFont', 8)).pack(side='left', padx=(0, 1))

        self.similarity_threshold = tk.IntVar(value=97)
        similarity_scale = ttk.Scale(
            threshold_control,
            from_=80,
            to=100,
            orient='horizontal',
            variable=self.similarity_threshold,
            command=self.on_similarity_changed
        )
        similarity_scale.pack(side='left', fill='x', expand=True, padx=(0, 1))

        self.similarity_label = ttk.Label(threshold_control, text="97%", width=3, font=('TkDefaultFont', 8, 'bold'))
        self.similarity_label.pack(side='left')
        self.similarity_frame.pack_forget()  # Hidden by default

        # Options - stacked vertically to save width
        ttk.Checkbutton(scan_frame, text="[FAST] Load fast",
                       variable=self.incremental_load,
                       command=self._on_incremental_toggle).pack(anchor='w', pady=(2, 0))

        ttk.Checkbutton(scan_frame, text="[ACTION] Skip videos",
                       variable=self.skip_videos).pack(anchor='w')

        ttk.Checkbutton(scan_frame, text="[Compact] Compact layout",
                   variable=self.compact_layout).pack(anchor='w')

        # Scan button
        self.scan_button = ttk.Button(scan_frame, text="[SEARCH] Find Duplicates",
                  command=self.start_duplicate_scan,
                  style='Accent.TButton')
        self.scan_button.pack(fill='x', pady=(2, 2))

        # Progress bar
        self.progress_bar = ttk.Progressbar(scan_frame, variable=self.scan_progress, mode='determinate')
        self.progress_bar.pack(fill='x', pady=(0, 2))

        # Results summary - very compact
        results_frame = ttk.Frame(scan_frame)
        results_frame.pack(fill='x')

        ttk.Label(results_frame, text="Groups:", font=('TkDefaultFont', 8)).pack(side='left')
        ttk.Label(results_frame, textvariable=self.total_duplicates, font=('TkDefaultFont', 8, 'bold')).pack(side='left', padx=(1, 4))
        ttk.Label(results_frame, text="Files:", font=('TkDefaultFont', 8)).pack(side='left')
        ttk.Label(results_frame, textvariable=self.total_files, font=('TkDefaultFont', 8, 'bold')).pack(side='left', padx=(1, 4))
        ttk.Label(results_frame, text="Batch:", font=('TkDefaultFont', 8)).pack(side='left')
        self.batch_size_var = tk.IntVar(value=50)
        self.batch_spin = ttk.Spinbox(results_frame, from_=10, to=500, increment=10,
                                 textvariable=self.batch_size_var, width=3, font=('TkDefaultFont', 8))
        self.batch_spin.pack(side='left', padx=(1, 0))

        # Scanning status indicator
        self.scanning_note = ttk.Label(scan_frame, text="[IDEA] Groups appear as found!",
                                       font=('TkDefaultFont', 8), foreground='#666', wraplength=220)

        # Auto Handle All button (only visible for view modes)
        self.auto_handle_all_button = ttk.Button(scan_frame, text="🤖 Auto Handle All",
                  command=self.auto_handle_all_groups,
                  style='Accent.TButton')
        # Will be shown/hidden based on hash_column selection

        # ===== STEP 2: Navigation =====
        nav_frame = ttk.LabelFrame(parent, text="2⃣ Navigation", padding=2)
        nav_frame.pack(fill='x', pady=(0, 2))

        # Current group indicator
        ttk.Label(nav_frame, textvariable=self.current_group_var,
                 font=('TkDefaultFont', 8, 'bold')).pack(pady=(0, 2))

        # Navigation buttons - compact row
        nav_buttons = ttk.Frame(nav_frame)
        nav_buttons.pack(fill='x')

        self.prev_button = ttk.Button(nav_buttons, text="<- Prev",
                                     command=self.previous_group, width=6)
        self.prev_button.pack(side='left', padx=(0, 1))

        self.next_button = ttk.Button(nav_buttons, text="Next ->",
                                     command=self.next_group, width=6)
        self.next_button.pack(side='left', padx=(0, 1))

        ttk.Button(nav_buttons, text="[NEXT] Skip",
                  command=self.next_group, width=6).pack(side='left')

        # ===== Quick Tools =====
        tools_frame = ttk.LabelFrame(parent, text="3⃣ Tools", padding=2)
        tools_frame.pack(fill='x', pady=(0, 2))

        # Single row of tool buttons
        tools_row = ttk.Frame(tools_frame)
        tools_row.pack(fill='x')

        ttk.Button(tools_row, text=" Save",
                  command=self.save_current_session, width=6).pack(side='left', padx=(0, 1))

        ttk.Button(tools_row, text="[FOLDER] Load",
                  command=self.load_saved_session, width=6).pack(side='left', padx=(0, 1))

        ttk.Button(tools_row, text="[FILE] Export",
                  command=self.export_report, width=6).pack(side='left', padx=(0, 1))

        ttk.Checkbutton(tools_row, text="[FAST] Skip confirm",
                       variable=self.skip_confirmations).pack(side='left', padx=(3, 0))

        # ===== Operation Log =====
        log_frame = ttk.LabelFrame(parent, text="[Clipboard] Log", padding=1)
        log_frame.pack(fill='both', expand=True, pady=(0, 2))

        self.log_text = tk.Text(log_frame, height=3, wrap='word', font=('Courier', 8), width=25)
        log_scroll = ttk.Scrollbar(log_frame, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.pack(side='left', fill='both', expand=True)
        log_scroll.pack(side='right', fill='y')

        # Status bar
        status_frame = ttk.Frame(parent)
        status_frame.pack(fill='x')

        self.status_label = ttk.Label(status_frame, textvariable=self.status_text,
                                      relief='sunken', anchor='w', padding=1, font=('TkDefaultFont', 8), wraplength=240)
        self.status_label.pack(fill='x')

    def create_preview_panel(self, parent):
        """Create the preview panel on the right side."""
        # Header with instructions
        header_frame = ttk.Frame(parent)
        header_frame.pack(fill='x', pady=(0, 10))

        ttk.Label(header_frame, text="[SNAPSHOT] Duplicate Files - Create Hybrid",
                 font=('TkDefaultFont', 11, 'bold')).pack(anchor='w')

        instruction_text = ("Review files and select attributes for the hybrid:\n"
                          "* Content (Pixels) - Which file's actual image/video to use\n"
                          "* Filename, Location, Date - Mix and match from any file\n"
                          "Click 'Process' to create hybrid and move remaining duplicates")
        ttk.Label(header_frame, text=instruction_text,
                 font=('TkDefaultFont', 8),
                 foreground='#555',
                 justify='left').pack(anchor='w', pady=(5, 0))

        # Hash info (shown when viewing a group)
        hash_frame = ttk.Frame(parent)
        hash_frame.pack(fill='x', pady=(0, 10))

        ttk.Label(hash_frame, text="[KEY] Hash:", font=('TkDefaultFont', 9, 'bold')).pack(side='left')
        self.hash_label = ttk.Label(hash_frame, text="", font=('TkMonospaceFont', 8), foreground='#666')
        self.hash_label.pack(side='left', padx=(5, 0))

        # Scrollable frame for file previews
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Update scroll region when frame changes
        def configure_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        self.scrollable_frame.bind("<Configure>", configure_scroll_region)

        # Mouse wheel scrolling
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind("<MouseWheel>", on_mousewheel)

    def bind_events(self):
        """Bind keyboard shortcuts and events."""
        self.root.bind('<Left>', lambda e: self.previous_group())
        self.root.bind('<Right>', lambda e: self.next_group())

    def on_hash_type_changed(self, event=None):
        """Handle hash type selection change."""
        # Get selected display name and convert to column name
        selected_display = self.hash_type_var.get()
        new_hash_column = self.hash_type_reverse_mapping.get(selected_display, 'perceptual_hash_phash')

        # Show/hide similarity controls based on mode
        # Similarity controls are for the multi-algorithm mode or for any perceptual hash
        if new_hash_column == 'perceptual_hash_similar' or new_hash_column.startswith('perceptual_hash_'):
            self.similarity_frame.pack(fill='x', pady=(2, 2))
        else:
            self.similarity_frame.pack_forget()

        # Show/hide Auto Handle All button for view modes
        if new_hash_column.startswith('view_'):
            self.auto_handle_all_button.pack(fill='x', pady=(2, 2))
        else:
            self.auto_handle_all_button.pack_forget()

        # Check if actually changed
        if new_hash_column == self.hash_column:
            return

        # Confirm if there are existing results
        if self.duplicate_groups:
            msg = (f"Switch to {selected_display}?\n\n"
                   "This will clear current results and require a new scan.")
            if not messagebox.askyesno("Change Detection Method", msg):
                # Revert selection
                self.hash_type_var.set(self.hash_type_mapping[self.hash_column])
                return

        # Update hash column
        self.hash_column = new_hash_column
        self.hash_column_display = self.hash_type_mapping[new_hash_column]

        # Update window title
        self.root.title(f"media sorter - Duplicate Manager ({self.hash_column_display})")

        # Clear existing results
        self.duplicate_groups = []
        self.current_group_index = 0
        self.total_duplicates.set(0)
        self.total_files.set(0)
        self.current_group_var.set("")

        # Clear preview area
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        self.hash_label.config(text="")

        # Update navigation state
        self.update_navigation_state()

        # Log the change
        self.log_message(f"Switched to {self.hash_column_display}")
        self.status_text.set(f"Ready - Using {self.hash_column_display}")

    def _on_incremental_toggle(self):
        """Handle incremental loading toggle - disable batch size when loading all at once."""
        if self.incremental_load.get():
            # Incremental loading enabled - allow batch size selection
            self.batch_spin.config(state='normal')
        else:
            # Load all at once - batch size is irrelevant
            self.batch_spin.config(state='disabled')

    def on_similarity_changed(self, value):
        """Handle similarity threshold slider change."""
        threshold = int(float(value))
        self.similarity_label.config(text=f"{threshold}%")

    def start_duplicate_scan(self):
        """Start scanning for duplicates in a background thread."""
        def scan_worker():
            try:
                self.is_scanning = True
                self.status_text.set("Scanning for duplicates...")
                self.scan_progress.set(0)

                # Show scanning note for similarity mode
                if self.hash_column == 'perceptual_hash_similar' or self.hash_column.startswith('perceptual_hash_'):
                    self.root.after(0, lambda: self.scanning_note.pack(fill='x', pady=(5, 0)))

                # Execute the duplicate query
                duplicates = self.execute_duplicate_query()

                # Process results into groups (will add incrementally for similarity mode)
                self.process_duplicate_results(duplicates)

                self.root.after(0, self.on_scan_complete)

            except Exception as e:
                logger.error(f"Error during duplicate scan: {e}")
                self.root.after(0, lambda: self.on_scan_error(str(e)))

        # Disable scan button and update text
        self.scan_button.config(state='disabled', text='[TIME] Scanning...')

        # Start scan in background thread
        threading.Thread(target=scan_worker, daemon=True).start()

    def execute_duplicate_query(self) -> List[Dict]:
        """Execute the SQL query to find duplicates using configured hash column."""
        try:
            # Check if we're using view-based detection
            if self.hash_column.startswith('view_'):
                return self.execute_view_based_query()

            # Check if we're in similarity mode (multi-algorithm or single perceptual hash)
            if self.hash_column == 'perceptual_hash_similar' or self.hash_column.startswith('perceptual_hash_'):
                return self.execute_similarity_search()

            # Otherwise do exact match query
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # Build query with dynamic hash column
            hash_col = self.hash_column

            # Build list of tables to include based on skip_videos setting
            skip_videos = self.skip_videos.get()

            # Define all tables
            all_tables = [
                ('archive_premium', 'archive_premium'),
                ('favorites', 'favorites'),
                ('manual_sort_images', 'manual_sort_images'),
                ('manual_sort_videos', 'manual_sort_videos'),
                ('sfw_image', 'sfw_image'),
                ('sfw_video', 'sfw_video'),
                ('sfw_gif', 'sfw_gif'),
                ('nsfw_image', 'nsfw_image'),
                ('nsfw_video', 'nsfw_video'),
                ('nsfw_gif', 'nsfw_gif'),
                ('archive_explicit_images', 'archive_explicit_images'),
                ('archive_explicit_videos', 'archive_explicit_videos'),
                ('archive_explicit_gifs', 'archive_explicit_gifs'),
                ('archive_sfw_images', 'archive_sfw_images'),
                ('archive_sfw_videos', 'archive_sfw_videos'),
                ('archive_sfw_gifs', 'archive_sfw_gifs'),
                ('possible_missing_files', 'possible_missing_files'),
            ]

            if self.ignore_archive_premium:
                all_tables = [(name, table) for name, table in all_tables if table != 'archive_premium']

            # Filter out video tables if skip_videos is enabled
            video_tables = {'manual_sort_videos', 'sfw_video', 'nsfw_video', 'archive_explicit_videos', 'archive_sfw_videos'}
            tables_to_query = [
                (name, table) for name, table in all_tables
                if not (skip_videos and table in video_tables)
            ]

            # Build UNION ALL query dynamically
            # When comparing file names, strip extensions (from the last dot)
            if hash_col == 'file_name':
                hash_expression = "SUBSTRING_INDEX(file_name, '.', -1)"
                # Actually we want everything BEFORE the last dot, so we need to remove the extension
                hash_expression = "IF(LOCATE('.', file_name) > 0, SUBSTRING(file_name, 1, LENGTH(file_name) - LENGTH(SUBSTRING_INDEX(file_name, '.', -1)) - 1), file_name)"
            elif hash_col == 'e621_post_id':
                # e621_post_id is already a clean integer column
                hash_expression = hash_col
            else:
                hash_expression = hash_col

            union_parts = []
            for table_name, table in tables_to_query:
                # Include e621_post_id in SELECT if it exists
                select_columns = f"'{table_name}' AS table_name, {hash_expression} AS hash_value, file_path, file_modified_at, file_size, e621_post_id"

                union_parts.append(f"""
                SELECT {select_columns}
                FROM `{_sorter_db()}`.`{table}`
                WHERE {hash_col} IS NOT NULL AND {hash_col} != ''
                """)

            combined_query = " UNION ALL ".join(union_parts)

            # Build subquery for duplicate detection
            subquery_parts = []
            for table_name, table in tables_to_query:
                subquery_parts.append(f"""
                        SELECT {hash_expression} AS hash_value FROM `{_sorter_db()}`.`{table}`
                        WHERE {hash_col} IS NOT NULL AND {hash_col} != ''
                """)

            subquery = " UNION ALL ".join(subquery_parts)

            query = f"""
            SELECT *
            FROM (
                {combined_query}
            ) AS combined
            WHERE hash_value IN (
                SELECT hash_value
                FROM (
                    SELECT hash_value
                    FROM (
                        {subquery}
                    ) AS all_hashes
                    GROUP BY hash_value
                    HAVING COUNT(*) > 1
                ) AS duplicate_hashes
            )
            ORDER BY hash_value, table_name
            """

            cursor.execute(query)
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            # Additional filter: if skip_videos is enabled, filter out video files by extension
            # This handles cases where video files might be in tables like 'favorites'
            if skip_videos:
                before_count = len(results)
                results = [f for f in results if get_file_type(f['file_path']) != 'video']
                filtered_count = before_count - len(results)
                if filtered_count > 0:
                    logger.info(f"Filtered out {filtered_count} video files from results")

            return results

        except Exception as e:
            logger.error(f"Database query failed: {e}")
            raise

    def execute_similarity_search(self) -> List[Dict]:
        """Find similar images using hamming distance on perceptual hashes."""
        try:
            # Initial status
            self.root.after(0, lambda: self.status_text.set("[TIME] Starting similarity search..."))
            self.root.after(0, lambda: self.scan_progress.set(5))

            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor(dictionary=True)

            hash_col = self.hash_column
            skip_videos = self.skip_videos.get()

            all_tables = [
                ('archive_premium', 'archive_premium'),
                ('favorites', 'favorites'),
                ('manual_sort_images', 'manual_sort_images'),
                ('manual_sort_videos', 'manual_sort_videos'),
                ('sfw_image', 'sfw_image'),
                ('sfw_video', 'sfw_video'),
                ('sfw_gif', 'sfw_gif'),
                ('nsfw_image', 'nsfw_image'),
                ('nsfw_video', 'nsfw_video'),
                ('nsfw_gif', 'nsfw_gif'),
                ('archive_explicit_images', 'archive_explicit_images'),
                ('archive_explicit_videos', 'archive_explicit_videos'),
                ('archive_explicit_gifs', 'archive_explicit_gifs'),
                ('archive_sfw_images', 'archive_sfw_images'),
                ('archive_sfw_videos', 'archive_sfw_videos'),
                ('archive_sfw_gifs', 'archive_sfw_gifs'),
                ('possible_missing_files', 'possible_missing_files'),
            ]

            if self.ignore_archive_premium:
                all_tables = [(name, table) for name, table in all_tables if table != 'archive_premium']

            video_tables = {'manual_sort_videos', 'sfw_video', 'nsfw_video', 'archive_explicit_videos', 'archive_sfw_videos'}
            tables_to_query = [
                (name, table) for name, table in all_tables
                if not (skip_videos and table in video_tables)
            ]

            union_parts = []
            for table_name, table in tables_to_query:
                union_parts.append(f"""
                SELECT '{table_name}' AS table_name, {hash_col} AS hash_value, file_path, file_modified_at, file_size, e621_post_id
                FROM `{_sorter_db()}`.`{table}`
                WHERE {hash_col} IS NOT NULL AND {hash_col} != ''
                """)

            combined_query = " UNION ALL ".join(union_parts)

            query = f"""
            SELECT * FROM (
                {combined_query}
            ) AS all_files
            ORDER BY table_name, file_path
            """

            self.root.after(0, lambda: self.status_text.set("Loading files from database..."))
            cursor.execute(query)
            all_files = cursor.fetchall()
            cursor.close()
            conn.close()

            if self.ignore_archive_premium:
                all_files = [f for f in all_files if f.get('table_name') != 'archive_premium']

            if skip_videos:
                before_count = len(all_files)
                all_files = [f for f in all_files if get_file_type(f['file_path']) != 'video']
                filtered_count = before_count - len(all_files)
                if filtered_count > 0:
                    logger.info(f"Filtered out {filtered_count} video files from results")

            total_files = len(all_files)
            if total_files == 0:
                self.root.after(0, lambda: self.status_text.set("[OK] No hashed files available for comparison"))
                return []

            total_comparisons = (total_files * (total_files - 1)) // 2
            if total_files > 3000:
                logger.warning(f"Large dataset: {total_files} files = {total_comparisons:,} comparisons")
                self.root.after(0, lambda: self.status_text.set(f"[WARNING] Comparing {total_files} files ({total_comparisons:,} comparisons) - Please wait..."))
            else:
                self.root.after(0, lambda: self.status_text.set(f"Comparing {total_files} files for similarity..."))

            logger.info(f"Comparing {total_files} files for similarity ({total_comparisons:,} comparisons)")

            threshold = int(self.similarity_threshold.get())
            session_id = hashlib.md5(f"{total_files}_{threshold}_{time.time()}".encode()).hexdigest()

            checkpoint = self._load_checkpoint(session_id) if total_files > 5000 else None
            start_index = 0
            similar_pairs = []
            processed_files: set = set()
            comparisons_made = 0
            comparisons_skipped = 0

            if checkpoint and checkpoint['total_files'] == total_files and checkpoint['threshold'] == threshold:
                start_index = checkpoint['last_index'] + 1
                similar_pairs = [
                    (p['file1'], p['file2'], p['similarity'])
                    for p in checkpoint['similar_pairs']
                ]
                processed_files = checkpoint['processed_files']
                comparisons_made = checkpoint.get('comparisons_made', 0)
                comparisons_skipped = checkpoint.get('comparisons_skipped', 0)

                logger.info(f"[IMPORT] Resuming from checkpoint at file {start_index}/{total_files}")
                self.root.after(0, lambda: self.status_text.set(f"[IMPORT] Resuming scan from file {start_index:,}/{total_files:,}..."))
            else:
                self._cleanup_old_checkpoints()
                logger.info("[SEARCH] Starting new similarity scan")

            MAX_SEGMENT_COUNT = 16

            def compute_segment_plan(sample_hash: str, threshold_pct: int) -> Optional[List[Tuple[int, int]]]:
                if not sample_hash:
                    return None
                components = sample_hash.split('|')
                plan: List[Tuple[int, int]] = []
                for comp in components:
                    comp = comp.lower()
                    comp_len = len(comp)
                    if comp_len == 0:
                        return None
                    allowed = max(0, int(comp_len * (1 - threshold_pct / 100.0)))
                    segment_count = allowed + 1
                    if segment_count > MAX_SEGMENT_COUNT or segment_count > comp_len:
                        return None
                    plan.append((comp_len, segment_count))
                return plan

            def build_segment_keys(hash_value: str, plan: List[Tuple[int, int]]) -> Optional[List[Tuple[int, int, str]]]:
                components = hash_value.lower().split('|')
                if len(components) != len(plan):
                    return None
                keys: List[Tuple[int, int, str]] = []
                for comp_idx, (expected_len, segment_count) in enumerate(plan):
                    comp = components[comp_idx]
                    if len(comp) != expected_len:
                        return None
                    base_len, remainder = divmod(expected_len, segment_count)
                    start = 0
                    for seg_idx in range(segment_count):
                        seg_len = base_len + (1 if seg_idx < remainder else 0)
                        end = start + seg_len
                        segment = comp[start:end]
                        if segment:
                            keys.append((comp_idx, seg_idx, segment))
                        start = end
                return keys

            use_similarity_index = False
            segment_plan: Optional[List[Tuple[int, int]]] = None
            segment_keys_per_file: List[Optional[List[Tuple[int, int, str]]]] = [None] * total_files
            segment_buckets: Optional[Dict[Tuple[int, int, str], List[int]]] = None

            if total_files > 1:
                segment_plan = compute_segment_plan(all_files[0]['hash_value'], threshold)
                if segment_plan:
                    candidate_buckets = defaultdict(list)
                    key_cache: List[Optional[List[Tuple[int, int, str]]]] = []
                    plan_valid = True
                    for idx, row in enumerate(all_files):
                        keys = build_segment_keys(row['hash_value'], segment_plan)
                        if keys is None:
                            plan_valid = False
                            break
                        key_cache.append(keys)
                        for key in keys:
                            candidate_buckets[key].append(idx)
                    if plan_valid:
                        use_similarity_index = True
                        segment_keys_per_file = key_cache
                        segment_buckets = candidate_buckets
                        logger.info(f"Similarity index enabled (<= {MAX_SEGMENT_COUNT} segments per component)")
                    else:
                        logger.info("Similarity index disabled due to inconsistent hash lengths")
                else:
                    logger.info("Similarity index not applicable for current threshold/hash length")

            last_update_pct = 0
            last_update_file = 0
            last_checkpoint_save = start_index
            similar_count = len(similar_pairs)
            start_time = time.time()
            batch_limit = self.batch_size_var.get() if self.incremental_load.get() else float('inf')
            groups_found_this_session = 0
            batch_limit_reached = False

            for i, file1 in enumerate(all_files):
                if i < start_index:
                    continue

                expected_pairs = max(total_files - i - 1, 0)

                if file1['file_path'] in processed_files:
                    comparisons_skipped += expected_pairs
                    continue

                if total_files > 5000 and i - last_checkpoint_save >= 100:
                    last_checkpoint_save = i
                    self._save_checkpoint(session_id, i, threshold, total_files,
                                          similar_pairs, processed_files,
                                          comparisons_made, comparisons_skipped)

                progress_pct = int((i / total_files) * 90)
                files_remaining = total_files - i
                should_update = (progress_pct > last_update_pct) or (i - last_update_file >= 10)

                if should_update:
                    last_update_pct = progress_pct
                    last_update_file = i
                    elapsed = int(time.time() - start_time)
                    elapsed_str = f"{elapsed//60}m {elapsed%60}s" if elapsed >= 60 else f"{elapsed}s"

                    files_processed = max(i - start_index, 1)
                    avg_time_per_file = elapsed / files_processed
                    eta_seconds = int(avg_time_per_file * files_remaining)
                    eta_str = f"{eta_seconds//60}m {eta_seconds%60}s" if eta_seconds >= 60 else f"{eta_seconds}s"
                    eta_display = f" - ETA: {eta_str}" if files_remaining > 0 else ""

                    checkpoint_icon = "[SAVE]" if total_files > 5000 else ""
                    saved_str = f" (skipped {comparisons_skipped:,})" if comparisons_skipped else ""
                    msg = (f"{checkpoint_icon}[SEARCH] File {i+1:,}/{total_files:,} ({progress_pct}%) - "
                           f"{similar_count} groups - {elapsed_str}{eta_display}{saved_str}")
                    self.root.after(0, lambda p=progress_pct, m=msg: (self.scan_progress.set(p), self.status_text.set(m)))

                file1_type = get_file_type(file1['file_path'])
                hash1 = file1['hash_value']

                using_index_for_file = False
                if use_similarity_index and segment_buckets is not None:
                    keys = segment_keys_per_file[i]
                    if keys is not None:
                        candidate_indices = set()
                        for key in keys:
                            for other_idx in segment_buckets.get(key, []):
                                if other_idx > i:
                                    candidate_indices.add(other_idx)
                        candidate_list = sorted(candidate_indices)
                        skipped_by_index = max(expected_pairs - len(candidate_list), 0)
                        comparisons_skipped += skipped_by_index
                        if not candidate_list:
                            continue
                        candidate_iterable = candidate_list
                        using_index_for_file = True
                    else:
                        candidate_iterable = range(i + 1, total_files)
                else:
                    candidate_iterable = range(i + 1, total_files)

                if not using_index_for_file:
                    # For brute-force path, all remaining pairs are evaluated (expected_pairs already covers skips)
                    pass

                for j in candidate_iterable:
                    file2 = all_files[j]

                    if file2['file_path'] in processed_files:
                        comparisons_skipped += 1
                        continue

                    file2_type = get_file_type(file2['file_path'])
                    if file1_type != file2_type:
                        comparisons_skipped += 1
                        continue

                    comparisons_made += 1
                    hash2 = file2['hash_value']

                    if hash1 == hash2:
                        continue

                    similarity = similarity_percentage(hash1, hash2, min_similarity=threshold)

                    if similarity >= threshold:
                        similar_pairs.append((file1, file2, similarity))

                        if file1['file_path'] not in processed_files:
                            groups_found_this_session += 1

                        similar_count += 1
                        processed_files.add(file1['file_path'])
                        processed_files.add(file2['file_path'])
                        logger.info(f"Found similar images ({similarity:.1f}%): {os.path.basename(file1['file_path'])} <-> {os.path.basename(file2['file_path'])}")

                        if groups_found_this_session >= batch_limit:
                            logger.info(f"[OK] Batch limit reached: {groups_found_this_session} new groups found")
                            if total_files > 5000:
                                self._save_checkpoint(session_id, i, threshold, total_files,
                                                      similar_pairs, processed_files,
                                                      comparisons_made, comparisons_skipped)
                            batch_limit_reached = True
                            break

                if batch_limit_reached:
                    break

            self.root.after(0, lambda: self.status_text.set(f"Grouping {len(similar_pairs)} similar pairs..."))
            self.root.after(0, lambda: self.scan_progress.set(92))

            file_groups = {}  # file_path -> group_id
            group_members = {}  # group_id -> list of files
            next_group_id = 0

            for file1, file2, similarity in similar_pairs:
                path1 = file1['file_path']
                path2 = file2['file_path']

                group1 = file_groups.get(path1)
                group2 = file_groups.get(path2)

                if group1 is None and group2 is None:
                    # Neither file in a group yet, create new group
                    group_id = next_group_id
                    next_group_id += 1
                    file_groups[path1] = group_id
                    file_groups[path2] = group_id
                    group_members[group_id] = [file1, file2]

                elif group1 is not None and group2 is None:
                    # File1 has a group, add file2 to it
                    file_groups[path2] = group1
                    if not any(f['file_path'] == path2 for f in group_members[group1]):
                        group_members[group1].append(file2)

                elif group1 is None and group2 is not None:
                    # File2 has a group, add file1 to it
                    file_groups[path1] = group2
                    if not any(f['file_path'] == path1 for f in group_members[group2]):
                        group_members[group2].append(file1)

                elif group1 != group2:
                    # Both files in different groups, merge groups
                    # Move all files from group2 to group1
                    for file in group_members[group2]:
                        file_groups[file['file_path']] = group1
                        if not any(f['file_path'] == file['file_path'] for f in group_members[group1]):
                            group_members[group1].append(file)
                    del group_members[group2]            # Convert groups to results format (assign same hash to similar files)
            # Also update any groups that were modified during merging
            self.root.after(0, lambda: self.status_text.set(f"Finalizing {len(group_members)} groups..."))
            self.root.after(0, lambda: self.scan_progress.set(97))

            # Clear and rebuild all groups to ensure consistency after merging
            self.root.after(0, lambda: setattr(self, 'duplicate_groups', []))

            results = []
            all_groups_data = []  # Store group data for non-incremental loading

            for group_id, files in group_members.items():
                # Use group_id as the synthetic hash value
                synthetic_hash = f"similar_group_{group_id}"
                files_with_hash = []
                for file in files:
                    file_copy = file.copy()
                    file_copy['hash_value'] = synthetic_hash
                    file_copy['original_hash'] = file['hash_value']  # Keep original for reference
                    files_with_hash.append(file_copy)
                    results.append(file_copy)

                # Store for potential batch loading
                all_groups_data.append((synthetic_hash, files_with_hash))

                # Add to UI incrementally if enabled
                self.root.after(0, lambda h=synthetic_hash, fs=files_with_hash: self.add_group_incrementally(h, fs))

            # If incremental loading is disabled, load all groups at once after scan completes
            if not self.incremental_load.get() and all_groups_data:
                def load_all_groups():
                    for hash_value, files in all_groups_data:
                        valid_files = [f for f in files if os.path.exists(f['file_path'])]
                        if len(valid_files) > 1:
                            group = DuplicateGroup(hash_value, valid_files, self.config)
                            self.duplicate_groups.append(group)

                    # Update counters
                    self.total_duplicates.set(len(self.duplicate_groups))
                    total_files = sum(len(g.files) for g in self.duplicate_groups)
                    self.total_files.set(total_files)

                    # Show first group
                    if len(self.duplicate_groups) > 0:
                        self.current_group_index = 0
                        self.display_current_group()
                        self.update_navigation_state()

                self.root.after(0, load_all_groups)

            self.root.after(0, lambda: self.scan_progress.set(100))

            # Determine if this was a partial scan (batch limit reached)
            scan_completed = groups_found_this_session < batch_limit

            # Clear checkpoint only if scan fully completed
            if total_files > 5000 and scan_completed:
                self._clear_checkpoint(session_id)
                logger.info("[OK] Checkpoint cleared after successful scan")
            elif not scan_completed:
                logger.info(f"[PAUSE] Batch scan paused - {groups_found_this_session} groups found (limit: {batch_limit})")

            # Log optimization statistics
            total_possible = (total_files * (total_files - 1)) // 2
            efficiency_gain = (comparisons_skipped / total_possible * 100) if total_possible > 0 else 0
            logger.info(f"Comparison stats: {comparisons_made:,} made, {comparisons_skipped:,} skipped ({efficiency_gain:.1f}% reduction)")

            if len(group_members) == 0:
                if scan_completed:
                    self.root.after(0, lambda: self.status_text.set(f"[OK] Scan complete: No similar files found (>={threshold}% similar)"))
                    logger.info(f"Similarity search complete: No similar files found at {threshold}% threshold")
                else:
                    self.root.after(0, lambda: self.status_text.set(f"[OK] Batch complete: No more groups found in this batch"))
            else:
                saved_pct = f" ({efficiency_gain:.0f}% faster)" if efficiency_gain > 10 else ""
                if scan_completed:
                    self.root.after(0, lambda sp=saved_pct: self.status_text.set(f"[OK] Scan complete: {len(group_members)} groups with {len(results)} files{sp}"))
                else:
                    self.root.after(0, lambda sp=saved_pct: self.status_text.set(f"[OK] Batch ready: {len(group_members)} groups found - Click 'Find Next Batch' for more{sp}"))
                logger.info(f"Similarity search: {len(group_members)} groups, {len(results)} files")

            return results

        except Exception as e:
            logger.error(f"Similarity search failed: {e}", exc_info=True)
            self.root.after(0, lambda: self.status_text.set(f"Similarity search failed: {e}"))
            raise

    def execute_view_based_query(self) -> List[Dict]:
        """Execute duplicate query using all_files view with CTE."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # Extract the actual hash column from the view_ prefix
            # e.g., view_perceptual_hash_dhash -> perceptual_hash_dhash
            actual_hash_col = self.hash_column.replace('view_', '')

            # Map to the query for each hash type
            query = f"""
                WITH duplicate_hashes AS (
                    SELECT {actual_hash_col}
                    FROM all_files
                    WHERE {actual_hash_col} IS NOT NULL AND {actual_hash_col} != ''
                    GROUP BY {actual_hash_col}
                    HAVING COUNT(*) > 1
                )
                SELECT af.file_name,
                       af.perceptual_hash_dhash,
                       af.perceptual_hash_phash,
                       af.perceptual_hash_average,
                       af.perceptual_hash_whash,
                       af.file_size,
                       af.file_modified_at,
                       af.file_path,
                       af.md5_hash,
                       af.e621_post_id,
                       af.hash_type,
                       af.file_created_at,
                       af.table_name,
                       af.{actual_hash_col} AS hash_value
                FROM all_files AS af
                JOIN duplicate_hashes dh
                      ON af.{actual_hash_col} = dh.{actual_hash_col}
                ORDER BY af.{actual_hash_col}, af.table_name
            """

            logger.info(f"Executing view-based query for {actual_hash_col}")
            cursor.execute(query)
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            if self.ignore_archive_premium:
                results = [f for f in results if f.get('table_name') != 'archive_premium']

            # Filter out videos if skip_videos is enabled
            if self.skip_videos.get():
                before_count = len(results)
                results = [f for f in results if get_file_type(f['file_path']) != 'video']
                filtered_count = before_count - len(results)
                if filtered_count > 0:
                    logger.info(f"Filtered out {filtered_count} video files from results")

            logger.info(f"View-based query found {len(results)} duplicate files")
            return results

        except Exception as e:
            logger.error(f"View-based query failed: {e}", exc_info=True)
            raise

    def add_group_incrementally(self, hash_value: str, files: List[Dict]):
        """Add a duplicate group during scan (for live updates)."""
        # Check if incremental loading is enabled
        if not self.incremental_load.get():
            # Just store the group data without updating UI
            return

        # Check if files exist
        valid_files = [
            f for f in files
            if os.path.exists(f['file_path']) and not self._should_skip_table(f.get('table_name', ''))
        ]

        if len(valid_files) > 1:  # Only if there are actual duplicates
            group = DuplicateGroup(hash_value, valid_files, self.config)
            self.duplicate_groups.append(group)

            # Update counters
            self.total_duplicates.set(len(self.duplicate_groups))
            total_files = sum(len(g.files) for g in self.duplicate_groups)
            self.total_files.set(total_files)

            # If this is the first group and we're not already viewing one, show it
            if len(self.duplicate_groups) == 1 and self.current_group_index == 0:
                self.display_current_group()
                self.update_navigation_state()

    def process_duplicate_results(self, results: List[Dict]):
        """Process query results into DuplicateGroup objects."""
        groups_by_hash = {}
        missing_files = []

        for row in results:
            if self._should_skip_table(row.get('table_name', '')):
                continue

            # Check if file actually exists on disk
            file_path = row['file_path']
            if not os.path.exists(file_path):
                missing_files.append((row['table_name'], file_path))
                continue  # Skip files that don't exist

            hash_value = row['hash_value']  # Use generic hash_value column from query

            # Get file type to ensure we only compare same types
            file_type = get_file_type(file_path)

            # Create a composite key: hash + file type
            # This ensures videos only match videos, images only match images, etc.
            group_key = f"{hash_value}:{file_type}"

            if group_key not in groups_by_hash:
                groups_by_hash[group_key] = []
            groups_by_hash[group_key].append(row)

        # Clean up database entries for missing files
        if missing_files:
            self.log_message(f"Found {len(missing_files)} files in database that no longer exist")
            for table_name, file_path in missing_files:
                try:
                    self.remove_file_from_database({
                        'table_name': table_name,
                        'file_path': file_path
                    })
                    self.log_message(f"Cleaned up: {os.path.basename(file_path)}")
                except Exception as e:
                    logger.error(f"Failed to clean up {file_path}: {e}")

        # Create DuplicateGroup objects
        self.duplicate_groups = []
        total_files = 0

        for group_key, files in groups_by_hash.items():
            if len(files) > 1:  # Only groups with actual duplicates
                if self._is_non_duplicate_group(files):
                    logger.info(
                        "Skipping group previously marked non-duplicate: %s",
                        self._group_signature(files),
                    )
                    continue
                # Extract just the hash part (before the colon)
                hash_value = group_key.split(':')[0]
                group = DuplicateGroup(hash_value, files, self.config)
                self.duplicate_groups.append(group)
                total_files += len(files)

        # Update counters
        self.total_duplicates.set(len(self.duplicate_groups))
        self.total_files.set(total_files)

    def on_scan_complete(self):
        """Called when duplicate scan completes successfully."""
        self.is_scanning = False
        self.scan_progress.set(100)
        self.current_group_index = 0

        # Hide scanning note
        self.scanning_note.pack_forget()

        # Re-enable scan button
        self.scan_button.config(state='normal', text='[SEARCH] Start Scan')

        if self.duplicate_groups:
            self.status_text.set(f"Found {len(self.duplicate_groups)} duplicate groups")
            self.display_current_group()
            self.log_message(f"Scan complete: {len(self.duplicate_groups)} duplicate groups found")
        else:
            self.status_text.set("No duplicates found")
            self.log_message("Scan complete: No duplicates found")

        self.update_navigation_state()

    def on_scan_error(self, error_msg: str):
        """Called when duplicate scan encounters an error."""
        self.is_scanning = False

        # Hide scanning note
        self.scanning_note.pack_forget()

        # Re-enable scan button
        self.scan_button.config(state='normal', text='[SEARCH] Start Scan')

        self.status_text.set("Scan failed")
        messagebox.showerror("Scan Error", f"Failed to scan for duplicates:\n{error_msg}")

    def display_current_group(self):
        """Display the current duplicate group with side-by-side comparison and attribute selection."""
        if not self.duplicate_groups or self.current_group_index >= len(self.duplicate_groups):
            return

        group = self.duplicate_groups[self.current_group_index]

        # Get file type for this group (all files should be same type)
        if group.files:
            file_type = get_file_type(group.files[0]['file_path'])
            file_type_icon = {'video': '[VIDEO]', 'gif': '[FRAMES]', 'image': '[IMAGE]'}.get(file_type, '[FILE]')
            file_type_label = f" ({file_type_icon} {file_type.upper()})"
        else:
            file_type_label = ""

        # Update hash display with file type
        self.hash_label.config(text=group.hash_value[:16] + "..." + file_type_label)

        # Update navigation display
        self.current_group_var.set(f"Group {self.current_group_index + 1} of {len(self.duplicate_groups)}")

        # Clear previous previews
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        # Get sorted files (highest priority first)
        sorted_files = group.get_priority_sorted_files()
        if len(sorted_files) < 2:
            ttk.Label(self.scrollable_frame, text="Not enough files to compare",
                     font=('TkDefaultFont', 10)).pack(pady=20)
            return

        # Get up to 4 files to compare
        files_to_display = sorted_files[:4]  # Display up to 4 files
        num_files = len(files_to_display)

        logger.info(f"Displaying {num_files} files out of {len(sorted_files)} total duplicates")

        # === SIDE-BY-SIDE COMPARISON ===
        comparison_frame = ttk.Frame(self.scrollable_frame)
        comparison_frame.pack(fill='both', expand=True, pady=(0, 8))

        comparison_frame.columnconfigure(0, weight=1, uniform='col')
        comparison_frame.columnconfigure(1, weight=1, uniform='col')

        preview_size = self._get_preview_size(num_files)

        for idx, file_info in enumerate(files_to_display):
            title = "Keep · recommended" if idx == 0 else f"Option {idx + 1}"
            row = idx // 2
            col = idx % 2
            padx = (0, 4) if col == 0 else (4, 0)
            pady = (0, 4) if row == 0 else (4, 0)

            file_frame = ttk.LabelFrame(comparison_frame, text=title, padding=8)
            file_frame.grid(row=row, column=col, sticky='nsew', padx=padx, pady=pady)

            self._create_media_preview(file_frame, file_info['file_path'], max_size=preview_size)
            self._create_file_info_display(file_frame, file_info)

        # Suggested pairs for quick merge button (first two files)
        suggested_keep = sorted_files[0]
        other_file = sorted_files[1] if len(sorted_files) > 1 else sorted_files[0]

        # Determine recommended indices for attribute defaults
        premium_files = []
        for idx, file_info in enumerate(files_to_display):
            if file_info.get('table_name') == 'archive_premium':
                premium_files.append((idx, file_info))

        premium_idx = None
        if premium_files:
            if len(premium_files) == 1:
                premium_idx = premium_files[0][0]
            else:
                oldest_premium_idx = premium_files[0][0]
                try:
                    oldest_premium_mtime = os.stat(premium_files[0][1]['file_path']).st_mtime
                    for idx, file_info in premium_files[1:]:
                        mtime = os.stat(file_info['file_path']).st_mtime
                        if mtime < oldest_premium_mtime:
                            oldest_premium_mtime = mtime
                            oldest_premium_idx = idx
                    premium_idx = oldest_premium_idx
                except Exception:
                    premium_idx = premium_files[0][0]

        largest_idx = 0
        largest_size = files_to_display[0].get('file_size', 0)
        for idx, file_info in enumerate(files_to_display):
            file_size = file_info.get('file_size', 0)
            if file_size > largest_size:
                largest_size = file_size
                largest_idx = idx

        best_dir_idx = 0
        best_dir_priority = group._get_directory_priority(files_to_display[0]['file_path'].lower())
        for idx, file_info in enumerate(files_to_display):
            dir_priority = group._get_directory_priority(file_info['file_path'].lower())
            if dir_priority < best_dir_priority:
                best_dir_priority = dir_priority
                best_dir_idx = idx

        best_filename_idx = 0
        best_filename_priority = group._get_filename_priority(os.path.basename(files_to_display[0]['file_path']).lower())
        for idx, file_info in enumerate(files_to_display):
            filename_priority = group._get_filename_priority(os.path.basename(file_info['file_path']).lower())
            if filename_priority < best_filename_priority:
                best_filename_priority = filename_priority
                best_filename_idx = idx

        oldest_idx = 0
        try:
            oldest_mtime = os.stat(files_to_display[0]['file_path']).st_mtime
            for idx, file_info in enumerate(files_to_display):
                mtime = os.stat(file_info['file_path']).st_mtime
                if mtime < oldest_mtime:
                    oldest_mtime = mtime
                    oldest_idx = idx
        except Exception:
            oldest_idx = 0

        if premium_idx is not None:
            defaults = {
                'base': largest_idx,
                'filename': premium_idx,
                'location': premium_idx,
                'date': oldest_idx,
            }
        else:
            defaults = {
                'base': largest_idx,
                'filename': best_filename_idx,
                'location': best_dir_idx,
                'date': oldest_idx,
            }

        group.base_file_var = tk.StringVar(value=str(defaults['base']))
        group.filename_var = tk.StringVar(value=str(defaults['filename']))
        group.location_var = tk.StringVar(value=str(defaults['location']))
        group.date_var = tk.StringVar(value=str(defaults['date']))

        attr_label = ttk.Label(self.scrollable_frame,
                               text="Choose which attributes to keep from each option",
                               font=('TkDefaultFont', 9, 'bold'))
        attr_label.pack(pady=(10, 2), anchor='w')

        attr_note = ttk.Label(self.scrollable_frame,
                              text="Extension follows the content source automatically.",
                              font=('TkDefaultFont', 7), foreground='#666')
        attr_note.pack(anchor='w', pady=(0, 5))

        grid_frame = ttk.Frame(self.scrollable_frame)
        grid_frame.pack(fill='x', pady=(0, 8))

        grid_frame.columnconfigure(0, weight=1, minsize=140)
        for i in range(num_files):
            grid_frame.columnconfigure(i + 1, weight=2)

        ttk.Label(grid_frame, text="Attribute", font=('TkDefaultFont', 8, 'bold')).grid(
            row=0, column=0, sticky='w', padx=4, pady=(0, 4))

        for idx in range(num_files):
            header_text = "Keep"
            if idx == 0:
                header_text = "Keep · recommended"
            elif num_files > 1:
                header_text = f"Option {idx + 1}"
            ttk.Label(grid_frame, text=header_text, font=('TkDefaultFont', 8, 'bold')).grid(
                row=0, column=idx + 1, sticky='w', padx=4, pady=(0, 4))

        ttk.Separator(grid_frame, orient='horizontal').grid(
            row=1, column=0, columnspan=num_files + 1, sticky='ew', pady=(2, 4))

        def _add_radio_row(row_idx: int, label_text: str, variable: tk.StringVar,
                           recommended_idx: int, value_factory) -> None:
            ttk.Label(grid_frame, text=label_text, font=('TkDefaultFont', 8)).grid(
                row=row_idx, column=0, sticky='w', padx=4, pady=2)
            for idx, file_info in enumerate(files_to_display):
                base_text = value_factory(idx, file_info)
                if idx == recommended_idx:
                    display_text = f"{base_text} ★"
                else:
                    display_text = base_text
                button = ttk.Radiobutton(grid_frame, text=display_text, value=str(idx),
                                         variable=variable)
                button.grid(row=row_idx, column=idx + 1, sticky='w', padx=4, pady=2)

        def _size_value(idx, file_info):
            size_mb = file_info.get('file_size', 0) / (1024 * 1024)
            text = f"{size_mb:.1f} MB"
            if idx == largest_idx:
                text += " · largest"
            return text

        def _name_value(idx, file_info):
            filename = os.path.splitext(os.path.basename(file_info['file_path']))[0]
            display_text = self._truncate_text(filename, 32)
            if idx == best_filename_idx:
                display_text += " · preferred"
            return display_text

        def _location_value(idx, file_info):
            directory = self._truncate_text(os.path.dirname(file_info['file_path']), 36, from_start=False)
            if idx == best_dir_idx:
                directory += " · priority"
            return directory

        def _date_value(idx, file_info):
            try:
                mtime = os.stat(file_info['file_path']).st_mtime
                date_display = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
            except Exception:
                date_display = '--'
            if idx == oldest_idx:
                date_display += " · oldest"
            return date_display

        _add_radio_row(2, "[STYLE] Content", group.base_file_var, defaults['base'], _size_value)
        _add_radio_row(3, "[Notes] Filename", group.filename_var, defaults['filename'], _name_value)
        _add_radio_row(4, "[FOLDER] Location", group.location_var, defaults['location'], _location_value)
        _add_radio_row(5, "[DATE] Date", group.date_var, defaults['date'], _date_value)

        # Store files_to_display in group for processing
        group.displayed_files = files_to_display

        # Compact table of all files in this group
        table_frame = ttk.Frame(self.scrollable_frame)
        table_frame.pack(fill='x', pady=(4, 8))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ('name', 'size', 'location', 'table', 'modified')
        tree = ttk.Treeview(table_frame, columns=columns, show='headings',
                             height=min(max(len(sorted_files), 1), 7))
        tree.heading('name', text='File')
        tree.heading('size', text='Size')
        tree.heading('location', text='Directory')
        tree.heading('table', text='Table')
        tree.heading('modified', text='Modified')

        tree.column('name', width=230, anchor='w')
        tree.column('size', width=70, anchor='center')
        tree.column('location', width=200, anchor='w')
        tree.column('table', width=90, anchor='center')
        tree.column('modified', width=110, anchor='center')

        tree.tag_configure('primary', font=('TkDefaultFont', 9, 'bold'))

        scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')

        for idx, file_info in enumerate(sorted_files):
            display_name = self._truncate_text(os.path.basename(file_info['file_path']), 50)
            size_display = self._format_size(file_info.get('file_size', 0))
            dir_display = self._truncate_text(os.path.dirname(file_info['file_path']), 45, from_start=False)
            modified_display = self._format_modified_at(file_info)
            tags = ('primary',) if idx == 0 else ()
            tree.insert('', 'end', text=str(idx),
                        values=(display_name, size_display, dir_display,
                                file_info.get('table_name', ''), modified_display),
                        tags=tags)

        tree.bind('<Double-1>', lambda e, t=tree: self._on_tree_double_click(e, t, sorted_files))

        ttk.Label(table_frame, text="Double-click a row to open the file",
                  font=('TkDefaultFont', 7), foreground='#666').grid(row=1, column=0, sticky='w', pady=(3, 0))

        # === ACTION BUTTONS ===
        action_frame = ttk.Frame(self.scrollable_frame)
        action_frame.pack(fill='x', pady=(8, 0))

        ttk.Button(action_frame, text="Merge Selection",
                  command=lambda: self.process_group_with_attributes_multi(group, sorted_files),
                  style='Accent.TButton').pack(side='left', padx=(0, 8))

        # Add Auto Handle button for view modes (uses phash detection)
        if self.hash_column.startswith('view_'):
            ttk.Button(action_frame, text="Auto Handle",
                      command=lambda: self.auto_handle_current_group(group, sorted_files),
                      style='Accent.TButton').pack(side='left', padx=(0, 8))

        ttk.Button(action_frame, text="Skip Group",
                  command=self.next_group).pack(side='left', padx=(0, 8))

        ttk.Button(action_frame, text="Mark Not Duplicate",
              command=self.mark_current_group_not_duplicate).pack(side='left', padx=(0, 8))

        # Show additional files count if there are more than displayed
        if len(sorted_files) > num_files:
            remaining_count = len(sorted_files) - num_files
            info_label = ttk.Label(self.scrollable_frame,
                                  text=f"[INFO] Note: {remaining_count} additional duplicate(s) not shown will be moved to duplicates folder",
                                  font=('TkDefaultFont', 8), foreground='#666')
            info_label.pack(pady=(10, 0))

    def _create_file_info_display(self, parent: ttk.Frame, file_info: Dict):
        """Create file information display for comparison view."""
        info_frame = ttk.Frame(parent)
        info_frame.pack(fill='x', pady=(0, 10))

        ttk.Label(info_frame, text=f"[FOLDER] {os.path.basename(file_info['file_path'])}",
                 font=('TkDefaultFont', 9, 'bold'), wraplength=380).pack(anchor='w')

        ttk.Label(info_frame, text=f"[CHART] Size: {file_info.get('file_size', 0):,} bytes",
                 font=('TkDefaultFont', 8)).pack(anchor='w')

        ttk.Label(info_frame, text=f"[FOLDER] Table: {file_info['table_name']}",
                 font=('TkDefaultFont', 8)).pack(anchor='w')

        # Show e621_post_id if in e621_post_id mode
        if self.hash_column == 'e621_post_id':
            post_id = file_info.get('e621_post_id', 'None')
            post_id_label = ttk.Label(info_frame, text=f"[NUMBER] Post ID: {post_id}",
                     font=('TkDefaultFont', 8))
            post_id_label.pack(anchor='w')

            # Add edit buttons
            edit_frame = ttk.Frame(info_frame)
            edit_frame.pack(anchor='w', pady=(5, 0))

            ttk.Button(edit_frame, text="[EDIT] Edit Post ID",
                      command=lambda: self._edit_post_id(file_info),
                      width=15).pack(side='left', padx=(0, 5))

            ttk.Button(edit_frame, text="[EDIT] Edit Filename",
                      command=lambda: self._edit_filename(file_info),
                      width=15).pack(side='left')

        # Show full path truncated
        full_path = file_info['file_path']
        if len(full_path) > 50:
            full_path = "..." + full_path[-47:]
        ttk.Label(info_frame, text=f"[LOCATION] {full_path}",
                 font=('TkDefaultFont', 7), foreground='#666').pack(anchor='w')

    def _truncate_text(self, text: str, max_length: int, from_start: bool = True) -> str:
        """Truncate text with ellipsis."""
        if len(text) <= max_length:
            return text
        if from_start:
            return text[:max_length-3] + "..."
        else:
            return "..." + text[-(max_length-3):]

    def _get_preview_size(self, file_count: int) -> tuple:
        """Return preview size adjusted for layout and file count."""
        base = 260 if self.compact_layout.get() else 340
        if file_count > 2:
            base = int(base * 0.85)
        return (base, base)

    def _format_size(self, size_bytes: int) -> str:
        """Format file size into human readable string."""
        if not size_bytes:
            return "--"
        if size_bytes >= 1024 ** 3:
            return f"{size_bytes / (1024 ** 3):.2f} GB"
        if size_bytes >= 1024 ** 2:
            return f"{size_bytes / (1024 ** 2):.1f} MB"
        if size_bytes >= 1024:
            return f"{size_bytes / 1024:.0f} KB"
        return f"{size_bytes} B"

    def _format_modified_at(self, file_info: Dict) -> str:
        """Return display-friendly modified timestamp for a file."""
        path = file_info.get('file_path')
        try:
            if path and os.path.exists(path):
                mtime = os.path.getmtime(path)
                return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
        except Exception:
            pass

        value = file_info.get('file_modified_at')
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M')
        if isinstance(value, str):
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
                try:
                    return datetime.strptime(value, fmt).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    continue
        return '--'

    def _resolve_selection_index(self, group, selected_value: str, fallback: int = 0) -> int:
        """Convert selection value to index, defaulting on failure."""
        if selected_value is None:
            return fallback
        value = str(selected_value).strip()
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _get_auto_merge_files(self, group: DuplicateGroup, files: List[Dict]) -> Tuple[Dict, Dict, Dict, Dict]:
        """Return the UI's auto-selected content, filename, location, and date files."""
        if not files:
            raise ValueError("No files available")

        premium_files = [f for f in files if f.get('table_name') == 'archive_premium']
        oldest_file = min(files, key=lambda f: os.stat(f['file_path']).st_mtime)
        largest_file = max(files, key=lambda f: f.get('file_size', 0))

        if premium_files:
            premium_file = min(premium_files, key=lambda f: os.stat(f['file_path']).st_mtime)
            return largest_file, premium_file, premium_file, oldest_file

        filename_file = min(
            files,
            key=lambda f: group._get_filename_priority(os.path.basename(f['file_path']).lower())
        )
        location_file = min(
            files,
            key=lambda f: group._get_directory_priority(f['file_path'].lower())
        )
        return largest_file, filename_file, location_file, oldest_file

    def _on_tree_double_click(self, event, tree: ttk.Treeview, files: List[Dict]):
        """Open file from table on double click."""
        item_id = tree.focus()
        if not item_id:
            return
        try:
            idx = int(tree.item(item_id, 'text'))
        except (ValueError, TypeError):
            return
        if 0 <= idx < len(files):
            self._open_file_in_viewer(files[idx]['file_path'])

    def _on_layout_toggle(self):
        """Refresh preview when compact layout preference changes."""
        if self.duplicate_groups:
            self.display_current_group()

    def _create_media_preview(self, parent: ttk.Frame, file_path: str, max_size: tuple = (300, 300)):
        """Create appropriate media preview widget based on file type."""
        ext = os.path.splitext(file_path)[1].lower()
        is_video = ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']

        if is_video:
            audio_flag = video_has_audio(file_path)
            badge_text = "Audio: Unknown"
            badge_color = '#666'
            if audio_flag is True:
                badge_text = "Audio: Yes"
                badge_color = '#007700'
            elif audio_flag is False:
                badge_text = "Audio: None"
                badge_color = '#aa0000'

            badge = ttk.Label(parent, text=badge_text, foreground=badge_color,
                              font=('TkDefaultFont', 8, 'bold'))
            badge.pack(anchor='w', padx=2, pady=(0, 2))

        preview_container = tk.Frame(parent, width=max_size[0], height=max_size[1])
        preview_container.pack(pady=(0, 10))
        preview_container.pack_propagate(False)

        if ext == '.gif':
            # Animated GIF
            gif_widget = AnimatedGifWidget(preview_container, file_path, self.preview_cache, max_size)
            gif_widget.pack(expand=True)
            gif_widget.bind('<Button-1>', lambda e: self._open_file_in_viewer(file_path))
            gif_widget.configure(cursor='hand2')
        elif is_video:
            # Video with player controls
            video_widget = VideoPlayerWidget(preview_container, file_path, self.preview_cache, max_size)
            video_widget.pack(expand=True, fill='both')
            video_widget.bind('<Button-1>', lambda e: self._open_file_in_viewer(file_path))
            video_widget.configure(cursor='hand2')
        else:
            # Static image
            preview = self.preview_cache.get_preview(file_path, max_size)
            if preview:
                img_label = ttk.Label(preview_container, image=preview, cursor='hand2')
                img_label.image = preview  # Keep reference
                img_label.pack(expand=True)
                img_label.bind('<Button-1>', lambda e: self._open_file_in_viewer(file_path))
            else:
                ttk.Label(preview_container, text="[No Preview]", font=('TkDefaultFont', 10),
                         foreground='#999').pack(expand=True)

    def _open_file_in_viewer(self, file_path: str):
        """Open file in the default system viewer."""
        try:
            if os.path.exists(file_path):
                # Use os.startfile on Windows to open with default app
                if sys.platform == 'win32':
                    os.startfile(file_path)
                elif sys.platform == 'darwin':  # macOS
                    import subprocess
                    subprocess.run(['open', file_path])
                else:  # Linux
                    import subprocess
                    subprocess.run(['xdg-open', file_path])
                logger.info(f"Opened file in default viewer: {file_path}")
            else:
                messagebox.showerror("Error", f"File not found:\n{file_path}")
        except Exception as e:
            logger.error(f"Error opening file: {e}")
            messagebox.showerror("Error", f"Could not open file:\n{str(e)}")

    def process_group_with_attributes_multi(self, group, sorted_files: List[Dict]):
        """Process the current group using selected attributes from multiple files.

        Args:
            group: The DuplicateGroup object with selection variables
            sorted_files: All files in the group (for moving remaining duplicates)
        """
        if not hasattr(group, 'displayed_files'):
            messagebox.showerror("Error", "No files available for processing")
            return

        displayed_files = group.displayed_files

        def finalize_group():
            """Remove the current group and refresh navigation/UI state."""
            if self.duplicate_groups and self.current_group_index < len(self.duplicate_groups):
                self.duplicate_groups.pop(self.current_group_index)

                if self.current_group_index >= len(self.duplicate_groups):
                    self.current_group_index = max(0, len(self.duplicate_groups) - 1)

                if self.duplicate_groups:
                    self.display_current_group()
                else:
                    for widget in self.scrollable_frame.winfo_children():
                        widget.destroy()
                    self.status_text.set("All duplicates processed")

                self.update_navigation_state()
                self.total_duplicates.set(len(self.duplicate_groups))

        # Get user selections (as indices)
        base_idx = self._resolve_selection_index(group, group.base_file_var.get(), 0)
        filename_idx = self._resolve_selection_index(group, group.filename_var.get(), 0)
        location_idx = self._resolve_selection_index(group, group.location_var.get(), 0)
        date_idx = self._resolve_selection_index(group, group.date_var.get(), 0)

        # Get the selected files
        base_file = displayed_files[base_idx]
        filename_file = displayed_files[filename_idx]
        location_file = displayed_files[location_idx]
        date_file = displayed_files[date_idx]

        # Simple case: every attribute points to the same file → keep it and move the rest
        if len({base_idx, filename_idx, location_idx, date_idx}) == 1:
            keep_file = base_file
            duplicates_to_move = [
                f for f in sorted_files
                if not self._files_equal(f, keep_file)
            ]

            moved_count = self.move_files_to_duplicates(duplicates_to_move)
            self.log_message(
                f"[OK] Kept single selection: {os.path.basename(keep_file['file_path'])}; moved {moved_count} duplicate(s)"
            )
            self.status_text.set("Ready")

            if not self.skip_confirmations.get():
                messagebox.showinfo(
                    "Processed",
                    f"Kept:\n{keep_file['file_path']}\nMoved {moved_count} duplicate(s) to duplicates folder.",
                )

            finalize_group()
            return

        # Simple relocate: only location differs → move kept file to that path, move others away
        if base_idx == filename_idx == date_idx and location_idx != base_idx:
            keep_file = base_file
            target_dir = os.path.dirname(location_file['file_path'])
            base_ext = os.path.splitext(keep_file['file_path'])[1]
            filename_base = os.path.splitext(os.path.basename(keep_file['file_path']))[0]
            requested_path = os.path.join(target_dir, f"{filename_base}{base_ext}")
            keep_stat = os.stat(keep_file['file_path'])

            duplicates_to_move = [
                f for f in sorted_files
                if not self._files_equal(f, keep_file)
            ]
            moved_count = self.move_files_to_duplicates(duplicates_to_move)

            os.makedirs(target_dir, exist_ok=True)
            final_path = requested_path

            counter = 1
            while (
                os.path.exists(final_path)
                and os.path.abspath(final_path) != os.path.abspath(keep_file['file_path'])
            ):
                final_path = os.path.join(target_dir, f"{filename_base}_relocated_{counter}{base_ext}")
                counter += 1

            if os.path.abspath(final_path) != os.path.abspath(keep_file['file_path']):
                shutil.move(keep_file['file_path'], final_path)
                _set_file_timestamps(final_path, keep_stat.st_atime, keep_stat.st_mtime, keep_stat.st_ctime)
                self.update_file_path_in_database(
                    keep_file['table_name'],
                    keep_file['file_path'],
                    final_path,
                    os.path.basename(final_path),
                )
                keep_file['file_path'] = final_path

            self.log_message(
                f"[OK] Relocated kept file to selected path; moved {moved_count} duplicate(s)"
            )
            self.status_text.set("Ready")

            if not self.skip_confirmations.get():
                messagebox.showinfo(
                    "Processed",
                    f"Moved kept file to:\n{final_path}\nMoved {moved_count} duplicate(s) to duplicates folder.",
                )

            finalize_group()
            return

        # Get file details
        base_ext = os.path.splitext(base_file['file_path'])[1]
        chosen_filename_base = os.path.splitext(os.path.basename(filename_file['file_path']))[0]
        final_filename = chosen_filename_base + base_ext
        final_dir = os.path.dirname(location_file['file_path'])
        final_table = location_file['table_name']
        final_path = os.path.join(final_dir, final_filename)

        # Get date
        try:
            final_mtime = os.stat(date_file['file_path']).st_mtime
            final_atime = os.stat(date_file['file_path']).st_atime
        except:
            final_mtime = os.stat(base_file['file_path']).st_mtime
            final_atime = os.stat(base_file['file_path']).st_atime

        # Show confirmation
        if not self.skip_confirmations.get():
            msg = (f"Create merged file from {len(displayed_files)} duplicates?\n\n"
                   f"Base Content: {os.path.basename(base_file['file_path'])}\n"
                   f"Filename: {os.path.basename(filename_file['file_path'])}\n"
                   f"Location: {os.path.dirname(location_file['file_path'])}\n"
                   f"Date: {datetime.fromtimestamp(final_mtime).strftime('%Y-%m-%d %H:%M')}\n\n"
                   f"Final result: {final_filename}\n"
                   f"All {len(sorted_files)} duplicate files will be moved to duplicates folder.\n"
                   f"This operation can be undone.")

            if not messagebox.askyesno("Confirm Merge", msg):
                return

        try:
            # Get duplicates folder
            duplicates_folder = self.config.duplicate_directory
            os.makedirs(duplicates_folder, exist_ok=True)

            self.log_message(f"Processing {len(sorted_files)} duplicate files...")

            # Move all files to duplicates folder first
            moved_files = []
            for file_info in sorted_files:
                file_path = file_info['file_path']
                if os.path.exists(file_path):
                    file_name = os.path.basename(file_path)
                    dest_path = os.path.join(duplicates_folder, file_name)

                    # Handle name conflicts
                    counter = 1
                    while os.path.exists(dest_path):
                        name, ext = os.path.splitext(file_name)
                        dest_path = os.path.join(duplicates_folder, f"{name}_{counter}{ext}")
                        counter += 1

                    shutil.move(file_path, dest_path)
                    moved_files.append((file_path, dest_path, file_info))
                    self.log_message(f"  Moved: {file_name} -> duplicates")

            # Find the base file in moved files and copy it back to final location
            base_moved = None
            for original_path, moved_path, file_info in moved_files:
                if original_path == base_file['file_path']:
                    base_moved = moved_path
                    break

            if not base_moved or not os.path.exists(base_moved):
                raise Exception("Base file not found after moving to duplicates")

            # Copy base file to final location
            shutil.copy2(base_moved, final_path)
            self.log_message(f"  Created: {final_filename}")

            # Preserve the selected file's full timestamp information.
            try:
                final_ctime = os.stat(date_file['file_path']).st_ctime
            except Exception:
                final_ctime = final_mtime
            _set_file_timestamps(final_path, final_atime, final_mtime, final_ctime)

            # Regenerate hashes
            self.status_text.set("Regenerating hashes...")
            new_perceptual = self.generate_perceptual_hash(final_path)
            new_md5 = self.generate_md5_hash(final_path)

            # Update database - remove all old entries
            for original_path, moved_path, file_info in moved_files:
                self.remove_file_from_database(file_info)

            # Add new entry
            self.update_replaced_file_in_database(
                final_table,
                base_file['file_path'],
                final_path,
                final_filename,
                new_perceptual,
                new_md5
            )

            self.log_message(f"[OK] Merge complete: {final_filename}")
            self.status_text.set("Ready")

            if not self.skip_confirmations.get():
                messagebox.showinfo("Success", f"Merged file created successfully!\n\n{final_path}\n\nAll {len(sorted_files)} duplicates moved to duplicates folder.")

            finalize_group()

        except Exception as e:
            logger.error(f"Multi-file merge failed: {e}", exc_info=True)
            self.log_message(f"[ERROR] Merge failed: {e}")
            messagebox.showerror("Error", f"Merge failed:\n{e}")
            self.status_text.set("Ready")

    def process_group_with_attributes(self, suggested_keep: Dict, other_file: Dict):
        """Process the current group using the selected attributes."""
        if not self.duplicate_groups or self.current_group_index >= len(self.duplicate_groups):
            return

        group = self.duplicate_groups[self.current_group_index]

        # Get user selections
        base_choice = group.base_file_var.get()
        filename_choice = group.filename_var.get()
        location_choice = group.location_var.get()
        date_choice = group.date_var.get()

        # Execute the custom merge with selected attributes
        self.execute_custom_merge(
            suggested_keep, other_file, base_choice,
            filename_choice, location_choice, date_choice
        )

    def _files_equal(self, file1: Dict, file2: Dict) -> bool:
        """Compare two file info dictionaries for equality."""
        if not file1 or not file2:
            return False

        # Compare by path and hash_value for definitive match
        return (file1.get('file_path') == file2.get('file_path') and
                file1.get('hash_value') == file2.get('hash_value') and
                file1.get('table_name') == file2.get('table_name'))

    # ===== OLD SELECTION-BASED METHODS REMOVED =====
    # These methods supported the old checkbox queue system.
    # New immediate-action system doesn't need selection tracking.

    def previous_group(self):
        """Navigate to previous duplicate group."""
        if self.current_group_index > 0:
            self.current_group_index -= 1
            self.display_current_group()
            self.update_navigation_state()

    def next_group(self):
        """Navigate to next duplicate group."""
        if self.current_group_index < len(self.duplicate_groups) - 1:
            self.current_group_index += 1
            self.display_current_group()
            self.update_navigation_state()

    def update_navigation_state(self):
        """Update navigation button states."""
        has_groups = bool(self.duplicate_groups)

        self.prev_button.config(state='normal' if has_groups and self.current_group_index > 0 else 'disabled')
        self.next_button.config(state='normal' if has_groups and self.current_group_index < len(self.duplicate_groups) - 1 else 'disabled')

    def auto_select_best(self):
        """Automatically select the best file to keep based on priority and file size."""
        if not self.duplicate_groups or self.current_group_index >= len(self.duplicate_groups):
            return

        group = self.duplicate_groups[self.current_group_index]
        sorted_files = group.get_priority_sorted_files()

        if len(sorted_files) < 2:
            # Only one file, just keep it
            group.selected_to_keep = sorted_files[0]
            group.files_to_move = []
            self.display_current_group()
            return

        # Get the highest priority file and the largest file
        highest_priority_file = sorted_files[0]
        largest_file = max(sorted_files, key=lambda f: f.get('file_size', 0))

        # Check if they're the same file
        if self._files_equal(highest_priority_file, largest_file):
            # Same file, simple case - keep priority file, move rest
            group.selected_to_keep = highest_priority_file
            group.files_to_move = sorted_files[1:]
            group.file_to_replace_keep = None
            self.log_message(f"Auto-selected: Highest priority file is also largest")
        else:
            # Different files - check if priority file is smaller
            priority_size = highest_priority_file.get('file_size', 0)
            largest_size = largest_file.get('file_size', 0)

            if priority_size < largest_size:
                # Priority file is smaller - set up REPLACE operation
                # Keep the larger file, but mark priority file to replace it
                group.selected_to_keep = largest_file
                group.file_to_replace_keep = highest_priority_file
                # Move all others except the two involved in replace
                group.files_to_move = [f for f in sorted_files
                                      if not self._files_equal(f, highest_priority_file)
                                      and not self._files_equal(f, largest_file)]

                priority_name = os.path.basename(highest_priority_file['file_path'])
                largest_name = os.path.basename(largest_file['file_path'])
                self.log_message(f"Auto-selected: Replace '{largest_name}' with higher priority '{priority_name}' "
                               f"(smaller file in priority directory)")
            else:
                # Priority file is larger or same size - keep it normally
                group.selected_to_keep = highest_priority_file
                group.files_to_move = sorted_files[1:]
                group.file_to_replace_keep = None
                self.log_message(f"Auto-selected: Highest priority file")

        # Refresh display
        self.display_current_group()

        self.log_message(f"Auto-selection complete for group {self.current_group_index + 1}")

    def auto_handle_current_group(self, group, sorted_files: List[Dict], render_next_group: bool = True):
        """Automatically handle the current duplicate group using priority logic.

        This method:
        1. Checks for archive_premium files FIRST - they trump everything
        2. If no premium file, identifies the best file to keep (highest priority + largest)
        3. Uses that file's attributes (content, filename, location, date)
        4. Processes the group automatically without user interaction

        Args:
            group: The DuplicateGroup object
            sorted_files: All files in the group sorted by priority
        """
        if not sorted_files or len(sorted_files) < 2:
            self.log_message("[WARNING] Not enough files to auto-handle")
            return

        try:
            base_file, filename_file, location_file, date_file = self._get_auto_merge_files(group, sorted_files)
            keep_file = base_file
            premium_count = sum(1 for f in sorted_files if f.get('table_name') == 'archive_premium')
            if premium_count > 1:
                self.log_message(
                    f"🌟 Auto: Using oldest archive_premium ({premium_count} found) for filename/location, largest content, and oldest date"
                )
            elif premium_count == 1:
                self.log_message(
                    "🌟 Auto: Using archive_premium for filename/location, largest content, and oldest date"
                )
            else:
                self.log_message(
                    "🤖 Auto: Using largest content, best filename, best location, and oldest date"
                )

            base_ext = os.path.splitext(base_file['file_path'])[1]
            chosen_filename_base = os.path.splitext(os.path.basename(filename_file['file_path']))[0]
            final_filename = chosen_filename_base + base_ext
            final_dir = os.path.dirname(location_file['file_path'])
            final_table = location_file['table_name']
            final_path = os.path.join(final_dir, final_filename)

            # Get date
            try:
                date_stat = os.stat(date_file['file_path'])
                final_mtime = date_stat.st_mtime
                final_atime = date_stat.st_atime
                final_ctime = date_stat.st_ctime
            except:
                final_mtime = os.stat(base_file['file_path']).st_mtime
                final_atime = os.stat(base_file['file_path']).st_atime
                final_ctime = os.stat(base_file['file_path']).st_ctime

            # Check if the kept file is already in the right place
            if os.path.abspath(base_file['file_path']) == os.path.abspath(final_path):
                # File is already where it should be - just move the duplicates
                self.log_message(f"🤖 Auto: Best file already in place, moving {len(sorted_files) - 1} duplicates")

                _set_file_timestamps(base_file['file_path'], final_atime, final_mtime, final_ctime)

                duplicates_folder = self.config.duplicate_directory
                os.makedirs(duplicates_folder, exist_ok=True)

                # Move all other files to duplicates
                moved_count = 0
                for file_info in sorted_files:
                    if self._files_equal(file_info, keep_file):
                        continue  # Skip the file we're keeping

                    file_path = file_info['file_path']
                    if os.path.exists(file_path):
                        file_name = os.path.basename(file_path)
                        dest_path = os.path.join(duplicates_folder, file_name)

                        # Handle name conflicts
                        counter = 1
                        while os.path.exists(dest_path):
                            name, ext = os.path.splitext(file_name)
                            dest_path = os.path.join(duplicates_folder, f"{name}_{counter}{ext}")
                            counter += 1

                        shutil.move(file_path, dest_path)
                        moved_count += 1
                        self.log_message(f"  Moved: {file_name} -> duplicates")

                        # Remove from database
                        self.remove_file_from_database(file_info)

                self.log_message(f"[OK] Auto-handled: Kept best file, moved {moved_count} duplicates")
            else:
                # Need to create merged file - use full processing logic
                self.log_message(f"🤖 Auto: Processing {len(sorted_files)} files into merged result")

                duplicates_folder = self.config.duplicate_directory
                os.makedirs(duplicates_folder, exist_ok=True)

                # Move all files to duplicates folder first
                moved_files = []
                for file_info in sorted_files:
                    file_path = file_info['file_path']
                    if os.path.exists(file_path):
                        file_name = os.path.basename(file_path)
                        dest_path = os.path.join(duplicates_folder, file_name)

                        # Handle name conflicts
                        counter = 1
                        while os.path.exists(dest_path):
                            name, ext = os.path.splitext(file_name)
                            dest_path = os.path.join(duplicates_folder, f"{name}_{counter}{ext}")
                            counter += 1

                        shutil.move(file_path, dest_path)
                        moved_files.append((file_path, dest_path, file_info))

                # Find the base file in moved files and copy it back to final location
                base_moved = None
                for original_path, moved_path, file_info in moved_files:
                    if original_path == base_file['file_path']:
                        base_moved = moved_path
                        break

                if not base_moved or not os.path.exists(base_moved):
                    raise Exception("Base file not found after moving to duplicates")

                # Copy base file to final location
                shutil.copy2(base_moved, final_path)

                # Preserve the selected file's full timestamp information.
                _set_file_timestamps(final_path, final_atime, final_mtime, final_ctime)

                # Regenerate hashes
                new_perceptual = self.generate_perceptual_hash(final_path)
                new_md5 = self.generate_md5_hash(final_path)

                # Update database - remove all old entries
                for original_path, moved_path, file_info in moved_files:
                    self.remove_file_from_database(file_info)

                # Add new entry
                self.update_replaced_file_in_database(
                    final_table,
                    base_file['file_path'],
                    final_path,
                    final_filename,
                    new_perceptual,
                    new_md5
                )

                self.log_message(f"[OK] Auto-handled: Created {final_filename}, moved {len(sorted_files)} to duplicates")

            # Remove processed group and move to next
            if self.duplicate_groups and self.current_group_index < len(self.duplicate_groups):
                self.duplicate_groups.pop(self.current_group_index)

                if self.current_group_index >= len(self.duplicate_groups):
                    self.current_group_index = max(0, len(self.duplicate_groups) - 1)

                if self.duplicate_groups and render_next_group:
                    self.display_current_group()
                else:
                    if not self.duplicate_groups:
                        for widget in self.scrollable_frame.winfo_children():
                            widget.destroy()
                        self.status_text.set("All duplicates processed")
                        self.log_message("[DONE] All duplicate groups auto-handled!")

                self.update_navigation_state()
                self.total_duplicates.set(len(self.duplicate_groups))

        except Exception as e:
            logger.error(f"Auto-handle failed: {e}", exc_info=True)
            self.log_message(f"[ERROR] Auto-handle failed: {e}")
            messagebox.showerror("Error", f"Auto-handle failed:\n{e}")

    def auto_handle_all_groups(self):
        """Automatically handle all duplicate groups in sequence.

        Processes each group using the auto-handle logic:
        - Keeps highest priority + largest file
        - Moves all duplicates to duplicates folder
        - Updates database accordingly
        """
        if not self.duplicate_groups:
            messagebox.showinfo("No Groups", "No duplicate groups to process.")
            return

        total_groups = len(self.duplicate_groups)

        # Confirm operation
        msg = (f"Auto-handle all {total_groups} duplicate groups?\n\n"
               f"This will:\n"
               f"* Keep the best file from each group (highest priority + largest)\n"
               f"* Move all duplicates to the duplicates folder\n"
               f"* Update the database\n\n"
               f"This operation can be time-consuming for many groups.\n"
               f"Continue?")

        if not messagebox.askyesno("Confirm Auto Handle All", msg):
            return

        self.log_message(f"🤖 Starting auto-handle for {total_groups} groups...")
        self.status_text.set(f"Processing {total_groups} groups...")

        # Batch mode: skip heavy preview rendering while auto-handling.
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        ttk.Label(
            self.scrollable_frame,
            text="Auto-handle in progress... Media previews are hidden for faster processing.",
            font=('TkDefaultFont', 10),
            foreground='#555'
        ).pack(pady=40)
        self.root.update_idletasks()

        processed_count = 0
        failed_count = 0

        # Disable buttons during processing
        self.scan_button.config(state='disabled')
        self.auto_handle_all_button.config(state='disabled')

        try:
            # Process groups from the beginning
            self.current_group_index = 0

            while self.duplicate_groups:
                group = self.duplicate_groups[0]  # Always process first group
                sorted_files = group.get_priority_sorted_files()

                # Update progress
                processed_count += 1
                self.status_text.set(f"Processing group {processed_count}/{total_groups}...")
                self.root.update()

                try:
                    # Auto-handle this group (it will remove itself from the list)
                    self.auto_handle_current_group(group, sorted_files, render_next_group=False)
                    self.log_message(f"  [OK] Group {processed_count}/{total_groups} complete")
                except Exception as e:
                    logger.error(f"Failed to auto-handle group {processed_count}: {e}")
                    self.log_message(f"  [ERROR] Group {processed_count} failed: {e}")
                    failed_count += 1
                    # Remove the failed group to continue
                    self.duplicate_groups.pop(0)

            # All done
            success_count = processed_count - failed_count
            self.log_message(f"[DONE] Auto-handle complete: {success_count} succeeded, {failed_count} failed")
            self.status_text.set(f"Auto-handle complete: {success_count}/{total_groups} successful")

            messagebox.showinfo("Auto Handle Complete",
                              f"Processed {total_groups} groups:\n"
                              f"[OK] Successful: {success_count}\n"
                              f"[ERROR] Failed: {failed_count}")

            # Keep preview pane lightweight after batch mode.
            for widget in self.scrollable_frame.winfo_children():
                widget.destroy()
            ttk.Label(
                self.scrollable_frame,
                text="Auto-handle complete. Scan again or load a saved session to continue.",
                font=('TkDefaultFont', 10),
                foreground='#555'
            ).pack(pady=40)

        except Exception as e:
            logger.error(f"Auto-handle all failed: {e}", exc_info=True)
            self.log_message(f"[ERROR] Auto-handle all failed: {e}")
            messagebox.showerror("Error", f"Auto-handle all failed:\n{e}")

        finally:
            # Re-enable buttons
            self.scan_button.config(state='normal')
            self.auto_handle_all_button.config(state='normal')
            self.update_navigation_state()

    def move_files_to_duplicates(self, files_to_move: List[Dict]) -> int:
        """Move files to the duplicates directory."""
        if not self.config or not hasattr(self.config, 'duplicate_directory'):
            messagebox.showerror("Configuration Error", "Duplicate directory not configured.")
            return 0

        duplicate_dir = self.config.duplicate_directory
        success_count = 0

        # Ensure duplicate directory exists
        os.makedirs(duplicate_dir, exist_ok=True)

        for file_info in files_to_move:
            try:
                source_path = file_info['file_path']

                if not os.path.exists(source_path):
                    self.log_message(f"Skipped missing file: {source_path}")
                    continue

                # Generate unique destination path
                filename = os.path.basename(source_path)
                dest_path = os.path.join(duplicate_dir, filename)

                # Handle naming conflicts
                counter = 1
                base_name, ext = os.path.splitext(filename)
                while os.path.exists(dest_path):
                    new_name = f"{base_name}_duplicate_{counter}{ext}"
                    dest_path = os.path.join(duplicate_dir, new_name)
                    counter += 1

                # Move the file
                shutil.move(source_path, dest_path)

                # Log the operation for undo
                # Use 'hash_value' which is what the query returns, or fallback to 'file_hash'
                hash_val = file_info.get('hash_value', file_info.get('file_hash', ''))

                self.operation_log.append({
                    'timestamp': datetime.now(),
                    'action': 'move',
                    'source': source_path,
                    'destination': dest_path,
                    'table': file_info['table_name'],
                    'hash': hash_val
                })

                # Remove from database
                self.remove_file_from_database(file_info)

                success_count += 1
                self.log_message(f"Moved: {filename} -> {dest_path}")

            except Exception as e:
                self.log_message(f"Failed to move {file_info['file_path']}: {e}")
                logger.error(f"Move failed for {file_info['file_path']}: {e}", exc_info=True)

        return success_count

    def remove_file_from_database(self, file_info: Dict):
        """Remove file entry from the database."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            table_name = file_info['table_name']
            file_path = file_info['file_path']

            # Normalize path to match database format (forward slashes)
            normalized_path = file_path.replace('\\', '/')

            query = f"DELETE FROM `{table_name}` WHERE `file_path` = %s"
            cursor.execute(query, (normalized_path,))

            rows_deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()

            if rows_deleted > 0:
                logger.info(f"Removed {rows_deleted} entry from database: {normalized_path}")
            else:
                logger.warning(f"No database entry found to remove: {normalized_path}")

        except Exception as e:
            logger.error(f"Failed to remove {file_path} from database: {e}")

    def export_report(self):
        """Export duplicate report to file."""
        if not self.duplicate_groups:
            if not self.skip_confirmations.get():
                messagebox.showinfo("No Data", "No duplicate groups to export.")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("CSV files", "*.csv"), ("All files", "*.*")]
        )

        if not filename:
            return

        try:
            if filename.endswith('.csv'):
                self.export_csv_report(filename)
            else:
                self.export_json_report(filename)

            if not self.skip_confirmations.get():
                messagebox.showinfo("Export Complete", f"Report exported to:\n{filename}")

        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export report:\n{e}")

    def export_json_report(self, filename: str):
        """Export duplicate report as JSON."""
        report_data = {
            'generated_at': datetime.now().isoformat(),
            'total_groups': len(self.duplicate_groups),
            'total_files': sum(len(group.files) for group in self.duplicate_groups),
            'groups': []
        }

        for i, group in enumerate(self.duplicate_groups):
            group_data = {
                'group_id': i + 1,
                'hash': group.hash_value,
                'file_count': len(group.files),
                'files': []
            }

            for file_info in group.files:
                file_data = {
                    'table': file_info['table_name'],
                    'path': file_info['file_path'],
                    'exists': os.path.exists(file_info['file_path'])
                }

                if file_data['exists']:
                    try:
                        stat = os.stat(file_info['file_path'])
                        file_data['size_bytes'] = stat.st_size
                        file_data['modified_at'] = datetime.fromtimestamp(stat.st_mtime).isoformat()
                    except:
                        pass

                group_data['files'].append(file_data)

            report_data['groups'].append(group_data)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

    def export_csv_report(self, filename: str):
        """Export duplicate report as CSV."""
        import csv

        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)

            # Header
            writer.writerow(['Group', 'Hash', 'Table', 'File Path', 'Exists', 'Size (MB)', 'Modified'])

            # Data rows
            for i, group in enumerate(self.duplicate_groups):
                for file_info in group.files:
                    exists = os.path.exists(file_info['file_path'])
                    size_mb = ""
                    modified = ""

                    if exists:
                        try:
                            stat = os.stat(file_info['file_path'])
                            size_mb = f"{stat.st_size / (1024 * 1024):.2f}"
                            modified = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            pass

                    writer.writerow([
                        i + 1,
                        group.hash_value,
                        file_info['table_name'],
                        file_info['file_path'],
                        'Yes' if exists else 'No',
                        size_mb,
                        modified
                    ])

    def update_file_path_in_database(self, table_name: str, old_path: str, new_path: str, new_filename: str):
        """Update file path and name in database."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            # Normalize paths
            old_path_normalized = old_path.replace('\\', '/')
            new_path_normalized = new_path.replace('\\', '/')

            # Check if updated_at column exists
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'updated_at'")
            has_updated_at = cursor.fetchone() is not None

            if has_updated_at:
                query = f"""
                UPDATE `{table_name}`
                SET file_path = %s, file_name = %s, updated_at = NOW()
                WHERE file_path = %s
                """
            else:
                query = f"""
                UPDATE `{table_name}`
                SET file_path = %s, file_name = %s
                WHERE file_path = %s
                """

            cursor.execute(query, (new_path_normalized, new_filename, old_path_normalized))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Updated database: {old_path} -> {new_path}")

        except Exception as e:
            logger.error(f"Failed to update database path: {e}")
            raise

    def regenerate_hash(self, file_info: Dict, hash_type: str):
        """Regenerate hash for a file and update database."""
        file_path = file_info['file_path']
        table_name = file_info['table_name']

        if not os.path.exists(file_path):
            messagebox.showerror("Error", f"File not found:\n{file_path}")
            return

        hash_type_display = {
            'perceptual_hash_phash': 'Perceptual Hash (phash)',
            'perceptual_hash_dhash': 'Difference Hash (dhash)',
            'perceptual_hash_average': 'Average Hash',
            'perceptual_hash_whash': 'Wavelet Hash (whash)',
            'md5_hash': 'MD5 Hash'
        }.get(hash_type, hash_type)

        # Confirm operation
        msg = f"Regenerate {hash_type_display} for:\n{os.path.basename(file_path)}\n\nThis will update the database."
        if not messagebox.askyesno("Confirm Regeneration", msg):
            return

        try:
            # Show progress
            self.status_text.set(f"Generating {hash_type_display}...")
            self.root.update()

            # Generate new hash
            if hash_type.startswith('perceptual_hash_'):
                new_hash = self.generate_perceptual_hash(file_path)
            elif hash_type == 'md5_hash':
                new_hash = self.generate_md5_hash(file_path)
            else:
                raise ValueError(f"Unknown hash type: {hash_type}")

            if not new_hash:
                messagebox.showerror("Error", f"Failed to generate {hash_type_display}")
                self.status_text.set("Ready")
                return

            # Update database
            self.update_hash_in_database(table_name, file_path, hash_type, new_hash)

            # Update file_info (use 'hash_value' as that's what the query returns)
            file_info['hash_value'] = new_hash
            if hash_type in file_info:
                file_info[hash_type] = new_hash

            self.log_message(f"Regenerated {hash_type_display}: {os.path.basename(file_path)}")
            self.status_text.set("Ready")

            if not self.skip_confirmations.get():
                messagebox.showinfo("Success", f"{hash_type_display} regenerated successfully!\n\nNew hash: {new_hash[:16]}...")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to regenerate hash:\n{e}")
            logger.error(f"Hash regeneration failed: {e}")
            self.status_text.set("Ready")

    def generate_perceptual_hash(self, file_path: str) -> Optional[str]:
        """Generate perceptual hash using media_hasher."""
        try:
            # fff: root media_hasher.hash_file is fff.hashing.hash_file; the ImportError fallback to
            # _handlers.media_hasher was dropped (fff.hashing is always importable).
            from fff.hashing import hash_file
            return hash_file(file_path)
        except Exception as e:
            logger.error(f"Failed to generate perceptual hash: {e}")
            return None

    def generate_md5_hash(self, file_path: str) -> Optional[str]:
        """Generate MD5 hash of file contents."""
        import hashlib
        try:
            md5 = hashlib.md5()
            with open(file_path, 'rb') as f:
                # Read in chunks for large files
                for chunk in iter(lambda: f.read(8192), b''):
                    md5.update(chunk)
            return md5.hexdigest()
        except Exception as e:
            logger.error(f"Failed to generate MD5 hash: {e}")
            return None

    def update_hash_in_database(self, table_name: str, file_path: str, hash_column: str, hash_value: str):
        """Update hash value in database."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            # Normalize path
            normalized_path = file_path.replace('\\', '/')

            # Check if updated_at column exists
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'updated_at'")
            has_updated_at = cursor.fetchone() is not None

            if has_updated_at:
                query = f"""
                UPDATE `{table_name}`
                SET {hash_column} = %s, updated_at = NOW()
                WHERE file_path = %s
                """
            else:
                query = f"""
                UPDATE `{table_name}`
                SET {hash_column} = %s
                WHERE file_path = %s
                """

            cursor.execute(query, (hash_value, normalized_path))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Updated {hash_column} for: {file_path}")

        except Exception as e:
            logger.error(f"Failed to update hash in database: {e}")
            raise

    def replace_keep_file_with_duplicate(self, duplicate_file_info: Dict):
        """Replace the keep file with this duplicate file.

        Process:
        1. Move keep file to duplicates folder
        2. Move this duplicate to keep file's location
        3. Rename duplicate to keep file's name
        4. Set modification time to match original keep file
        5. Update database entries
        """
        if not self.duplicate_groups or self.current_group_index >= len(self.duplicate_groups):
            return

        group = self.duplicate_groups[self.current_group_index]

        # Find the keep file
        if not group.selected_to_keep:
            # Auto-select the suggested keep file if none selected
            sorted_files = group.get_priority_sorted_files()
            if not sorted_files:
                messagebox.showerror("Error", "No files in group")
                return
            group.selected_to_keep = sorted_files[0]

        keep_file = group.selected_to_keep
        duplicate_file = duplicate_file_info

        # Can't replace itself
        if self._files_equal(keep_file, duplicate_file):
            if not self.skip_confirmations.get():
                messagebox.showinfo("Same File", "This is already the keep file")
            return

        # Check both files exist
        if not os.path.exists(keep_file['file_path']):
            messagebox.showerror("Error", f"Keep file not found:\n{keep_file['file_path']}")
            return

        if not os.path.exists(duplicate_file['file_path']):
            messagebox.showerror("Error", f"Duplicate file not found:\n{duplicate_file['file_path']}")
            return

        # Get details
        keep_path = keep_file['file_path']
        keep_filename = os.path.basename(keep_path)
        keep_dir = os.path.dirname(keep_path)

        duplicate_path = duplicate_file['file_path']
        duplicate_filename = os.path.basename(duplicate_path)

        # Confirm operation
        msg = (f"Replace keep file with duplicate?\n\n"
               f"Keep File (will be moved to duplicates):\n"
               f"  {keep_filename}\n\n"
               f"Duplicate (will replace keep file):\n"
               f"  {duplicate_filename}\n\n"
               f"The duplicate will take the keep file's:\n"
               f"  * Location: {keep_dir}\n"
               f"  * Name: {keep_filename}\n"
               f"  * Oldest modification date (between both files)\n"
               f"  * Regenerated hashes\n\n"
               f"This operation can be undone.")

        # Skip confirmation if checkbox is enabled
        if not self.skip_confirmations.get():
            if not messagebox.askyesno("Confirm Replace", msg):
                return

        try:
            # Get both files' modification times
            keep_stat = os.stat(keep_path)
            keep_mtime = keep_stat.st_mtime
            keep_atime = keep_stat.st_atime

            duplicate_stat = os.stat(duplicate_path)
            duplicate_mtime = duplicate_stat.st_mtime
            duplicate_atime = duplicate_stat.st_atime

            # Use the oldest modification time (earliest date)
            final_mtime = min(keep_mtime, duplicate_mtime)
            final_atime = min(keep_atime, duplicate_atime)

            oldest_date_str = datetime.fromtimestamp(final_mtime).strftime('%Y-%m-%d %H:%M:%S')

            self.log_message(f"Keep file date: {datetime.fromtimestamp(keep_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
            self.log_message(f"Duplicate file date: {datetime.fromtimestamp(duplicate_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
            self.log_message(f"Using oldest date: {oldest_date_str}")

            # Get duplicates folder from config
            if not self.config or not hasattr(self.config, 'duplicate_directory'):
                messagebox.showerror("Configuration Error", "Duplicate directory not configured.")
                return

            duplicates_folder = self.config.duplicate_directory

            # Ensure duplicates folder exists
            os.makedirs(duplicates_folder, exist_ok=True)

            # Generate unique name for moved keep file in duplicates folder
            keep_dest = os.path.join(duplicates_folder, keep_filename)
            counter = 1
            while os.path.exists(keep_dest):
                name, ext = os.path.splitext(keep_filename)
                keep_dest = os.path.join(duplicates_folder, f"{name}_{counter}{ext}")
                counter += 1

            # Step 1: Move keep file to duplicates folder
            self.status_text.set("Moving keep file to duplicates...")
            self.root.update()
            shutil.move(keep_path, keep_dest)
            self.log_message(f"Moved keep file: {keep_filename} -> {os.path.basename(keep_dest)}")

            # Step 2: Move duplicate file to keep file's location
            self.status_text.set("Moving duplicate to keep location...")
            self.root.update()
            shutil.move(duplicate_path, keep_path)
            self.log_message(f"Moved duplicate: {duplicate_filename} -> {keep_path}")

            final_ctime = min(keep_stat.st_ctime, duplicate_stat.st_ctime)

            # Step 3: Set timestamps to the oldest date between both files
            _set_file_timestamps(keep_path, final_atime, final_mtime, final_ctime)
            self.log_message(f"Set file timestamps to oldest: {oldest_date_str}")

            # Step 4: Regenerate all hashes for the new keep file
            self.status_text.set("Regenerating hashes for new keep file...")
            self.root.update()

            new_perceptual_hash = self.generate_perceptual_hash(keep_path)
            new_md5_hash = self.generate_md5_hash(keep_path)

            if new_perceptual_hash:
                self.log_message(f"Generated perceptual hash: {new_perceptual_hash[:16]}...")
            else:
                self.log_message("[WARNING] Failed to generate perceptual hash")

            if new_md5_hash:
                self.log_message(f"Generated MD5 hash: {new_md5_hash[:16]}...")
            else:
                self.log_message("[WARNING] Failed to generate MD5 hash")

            # Step 5: Update database
            # Remove old keep file entry from its table (it's been moved to duplicates)
            self.remove_file_from_database(keep_file)

            # Update duplicate file entry to new location with new hashes
            # This replaces the duplicate's database entry with the keep file's location and new hashes
            self.update_replaced_file_in_database(
                duplicate_file['table_name'],
                duplicate_path,
                keep_path,
                keep_filename,
                new_perceptual_hash,
                new_md5_hash
            )

            # Note: We do NOT add the moved keep file to database since it's now in duplicates folder
            # and we want to remove it from tracking
            self.log_message(f"Removed old keep file from database: {keep_filename}")

            # Log for undo capability
            operation = {
                'timestamp': datetime.now().isoformat(),
                'type': 'replace_keep',
                'keep_file': {
                    'original_path': keep_path,
                    'moved_to': keep_dest,
                    'table': keep_file['table_name'],
                    'hash': keep_file.get('hash_value', ''),
                    'old_md5_hash': keep_file.get('md5_hash', '')
                },
                'duplicate_file': {
                    'original_path': duplicate_path,
                    'moved_to': keep_path,
                    'table': duplicate_file['table_name'],
                    'hash': duplicate_file.get('hash_value', ''),
                    'new_perceptual_hash': new_perceptual_hash or '',
                    'new_md5_hash': new_md5_hash or ''
                },
                'original_mtime': keep_mtime
            }
            self.operation_log.append(operation)

            self.status_text.set("Ready")
            self.log_message(f"[OK] Successfully replaced keep file with: {duplicate_filename}")

            if not self.skip_confirmations.get():
                messagebox.showinfo("Success",
                                  f"Keep file replaced successfully!\n\n"
                                  f"Old keep file moved to:\n{keep_dest}\n\n"
                                  f"New keep file:\n{keep_path}")

            # Remove processed group (similar to move operation)
            if self.duplicate_groups and self.current_group_index < len(self.duplicate_groups):
                self.duplicate_groups.pop(self.current_group_index)

                # Adjust navigation
                if self.current_group_index >= len(self.duplicate_groups):
                    self.current_group_index = max(0, len(self.duplicate_groups) - 1)

                # Update display
                if self.duplicate_groups:
                    self.display_current_group()
                else:
                    # Clear preview area
                    for widget in self.scrollable_frame.winfo_children():
                        widget.destroy()
                    self.status_text.set("All duplicates processed")

                self.update_navigation_state()

        except Exception as e:
            logger.error(f"Failed to replace keep file: {e}")
            self.log_message(f"[ERROR] Failed to replace keep file: {e}")
            messagebox.showerror("Error", f"Failed to replace keep file:\n{e}")
            self.status_text.set("Ready")

    def swap_with_keep_file(self, this_file_info: Dict):
        """Swap locations of this file with the keep file.

        This allows you to keep a better quality file even if it's in a lower priority directory.
        Process:
        1. Move keep file to this file's location (with this file's name)
        2. Move this file to keep file's location (with keep file's name)
        3. Preserve oldest modification time for each
        4. Update database entries
        """
        if not self.duplicate_groups or self.current_group_index >= len(self.duplicate_groups):
            return

        group = self.duplicate_groups[self.current_group_index]

        # Find the keep file
        if not group.selected_to_keep:
            # Auto-select the suggested keep file if none selected
            sorted_files = group.get_priority_sorted_files()
            if not sorted_files:
                messagebox.showerror("Error", "No files in group")
                return
            group.selected_to_keep = sorted_files[0]

        keep_file = group.selected_to_keep
        swap_file = this_file_info

        # Can't swap with itself
        if self._files_equal(keep_file, swap_file):
            if not self.skip_confirmations.get():
                messagebox.showinfo("Same File", "This is already the keep file")
            return

        # Check both files exist
        if not os.path.exists(keep_file['file_path']):
            messagebox.showerror("Error", f"Keep file not found:\n{keep_file['file_path']}")
            return

        if not os.path.exists(swap_file['file_path']):
            messagebox.showerror("Error", f"File not found:\n{swap_file['file_path']}")
            return

        # Get details
        keep_path = keep_file['file_path']
        keep_filename = os.path.basename(keep_path)
        keep_dir = os.path.dirname(keep_path)

        swap_path = swap_file['file_path']
        swap_filename = os.path.basename(swap_path)
        swap_dir = os.path.dirname(swap_path)

        # Confirm operation
        msg = (f"Swap file locations?\n\n"
               f"Keep File ([STAR] higher priority):\n"
               f"  {keep_filename}\n"
               f"  Will move to: {swap_dir}\n"
               f"  Will be renamed to: {swap_filename}\n\n"
               f"This File (you prefer this one):\n"
               f"  {swap_filename}\n"
               f"  Will move to: {keep_dir}\n"
               f"  Will be renamed to: {keep_filename}\n\n"
               f"Result: Your preferred file will be in the higher priority location!\n\n"
               f"This operation can be undone.")

        # Skip confirmation if checkbox is enabled
        if not self.skip_confirmations.get():
            if not messagebox.askyesno("Confirm Swap", msg):
                return

        try:
            # Get both files' modification times
            keep_stat = os.stat(keep_path)
            keep_mtime = keep_stat.st_mtime
            keep_atime = keep_stat.st_atime

            swap_stat = os.stat(swap_path)
            swap_mtime = swap_stat.st_mtime
            swap_atime = swap_stat.st_atime

            self.log_message(f"Swapping file locations:")
            self.log_message(f"  Keep: {keep_filename} ({keep_dir})")
            self.log_message(f"  Swap: {swap_filename} ({swap_dir})")

            # Create temporary file in same directory as keep file for atomic swap
            import tempfile
            temp_dir = keep_dir
            temp_fd, temp_path = tempfile.mkstemp(dir=temp_dir, suffix=os.path.splitext(keep_filename)[1])
            os.close(temp_fd)  # Close the file descriptor
            os.unlink(temp_path)  # Remove the temp file (we just need the path)

            # Step 1: Move keep file to temp location
            self.status_text.set("Swapping files (1/3)...")
            self.root.update()
            shutil.move(keep_path, temp_path)
            self.log_message(f"Moved keep to temp: {temp_path}")

            # Step 2: Move swap file to keep's location with keep's name
            self.status_text.set("Swapping files (2/3)...")
            self.root.update()
            shutil.move(swap_path, keep_path)
            self.log_message(f"Moved swap to keep location: {swap_filename} -> {keep_path}")

            # Step 3: Move temp (original keep) to swap's location with swap's name
            self.status_text.set("Swapping files (3/3)...")
            self.root.update()
            shutil.move(temp_path, swap_path)
            self.log_message(f"Moved keep to swap location: {keep_filename} -> {swap_path}")

            # Step 4: Preserve full timestamps with their current files.
            _set_file_timestamps(keep_path, swap_atime, swap_mtime, swap_stat.st_ctime)
            _set_file_timestamps(swap_path, keep_atime, keep_mtime, keep_stat.st_ctime)
            self.log_message(f"Preserved file timestamps")

            # Step 5: Regenerate hashes for both files in their new locations
            self.status_text.set("Regenerating hashes...")
            self.root.update()

            # Regenerate for file now at keep location (was swap file)
            new_keep_perceptual = self.generate_perceptual_hash(keep_path)
            new_keep_md5 = self.generate_md5_hash(keep_path)

            # Regenerate for file now at swap location (was keep file)
            new_swap_perceptual = self.generate_perceptual_hash(swap_path)
            new_swap_md5 = self.generate_md5_hash(swap_path)

            # Step 6: Update database
            # Update the file now at keep location (was swap file)
            self.update_replaced_file_in_database(
                swap_file['table_name'],
                swap_file['file_path'],  # old path
                keep_path,  # new path
                keep_filename,  # new filename
                new_keep_perceptual,
                new_keep_md5
            )

            # Update the file now at swap location (was keep file)
            self.update_replaced_file_in_database(
                keep_file['table_name'],
                keep_file['file_path'],  # old path
                swap_path,  # new path
                swap_filename,  # new filename
                new_swap_perceptual,
                new_swap_md5
            )

            # Log for undo capability
            operation = {
                'timestamp': datetime.now().isoformat(),
                'type': 'swap_locations',
                'file1': {
                    'original_path': keep_path,
                    'new_path': swap_path,
                    'table': keep_file['table_name'],
                    'old_md5_hash': keep_file.get('md5_hash', ''),
                    'new_perceptual_hash': new_swap_perceptual or '',
                    'new_md5_hash': new_swap_md5 or '',
                    'mtime': keep_mtime
                },
                'file2': {
                    'original_path': swap_path,
                    'new_path': keep_path,
                    'table': swap_file['table_name'],
                    'old_md5_hash': swap_file.get('md5_hash', ''),
                    'new_perceptual_hash': new_keep_perceptual or '',
                    'new_md5_hash': new_keep_md5 or '',
                    'mtime': swap_mtime
                }
            }
            self.operation_log.append(operation)

            self.status_text.set("Ready")
            self.log_message(f"[OK] Successfully swapped file locations")

            if not self.skip_confirmations.get():
                messagebox.showinfo("Success",
                                  f"Files swapped successfully!\n\n"
                                  f"{swap_filename} is now at:\n{keep_path}\n\n"
                                  f"{keep_filename} is now at:\n{swap_path}")

            # Remove processed group
            if self.duplicate_groups and self.current_group_index < len(self.duplicate_groups):
                self.duplicate_groups.pop(self.current_group_index)

                # Adjust navigation
                if self.current_group_index >= len(self.duplicate_groups):
                    self.current_group_index = max(0, len(self.duplicate_groups) - 1)

                # Update display
                if self.duplicate_groups:
                    self.display_current_group()
                else:
                    # Clear preview area
                    for widget in self.scrollable_frame.winfo_children():
                        widget.destroy()
                    self.status_text.set("All duplicates processed")

                self.update_navigation_state()
                self.total_duplicates.set(len(self.duplicate_groups))

        except Exception as e:
            logger.error(f"Failed to swap files: {e}")
            self.log_message(f"[ERROR] Failed to swap files: {e}")
            messagebox.showerror("Error", f"Failed to swap files:\n{e}")
            self.status_text.set("Ready")
            # Note: Swap is atomic via temp file, so partial states are less likely

    def move_to_duplicates(self, file_path: str) -> str:
        """Move a single file to duplicates directory and return the destination path."""
        if not self.config or not hasattr(self.config, 'duplicate_directory'):
            raise ValueError("Duplicate directory not configured")

        duplicate_dir = self.config.duplicate_directory
        os.makedirs(duplicate_dir, exist_ok=True)

        filename = os.path.basename(file_path)
        dest_path = os.path.join(duplicate_dir, filename)

        # Handle naming conflicts
        counter = 1
        base_name, ext = os.path.splitext(filename)
        while os.path.exists(dest_path):
            new_name = f"{base_name}_duplicate_{counter}{ext}"
            dest_path = os.path.join(duplicate_dir, new_name)
            counter += 1

        # Move the file
        shutil.move(file_path, dest_path)
        return dest_path

    def execute_custom_merge(self, keep_file: Dict, other_file: Dict, base_choice: str,
                            filename_choice: str, location_choice: str, date_choice: str):
        """Execute the custom merge operation based on user selections."""
        try:
            # Determine which file to use as base (actual content)
            if base_choice == "keep":
                base_file = keep_file
                discard_file = other_file
            else:
                base_file = other_file
                discard_file = keep_file

            # Determine final filename
            # IMPORTANT: Always use extension from base file (actual content source)
            base_ext = os.path.splitext(base_file['file_path'])[1]

            if filename_choice == "keep":
                chosen_filename = os.path.basename(keep_file['file_path'])
            else:
                chosen_filename = os.path.basename(other_file['file_path'])

            # Replace extension with base file's extension to match content
            final_filename_base = os.path.splitext(chosen_filename)[0]
            final_filename = final_filename_base + base_ext

            # Determine final location
            if location_choice == "keep":
                final_dir = os.path.dirname(keep_file['file_path'])
                final_table = keep_file['table_name']
            else:
                final_dir = os.path.dirname(other_file['file_path'])
                final_table = other_file['table_name']

            # Determine final date
            keep_mtime = os.stat(keep_file['file_path']).st_mtime
            keep_atime = os.stat(keep_file['file_path']).st_atime
            other_mtime = os.stat(other_file['file_path']).st_mtime
            other_atime = os.stat(other_file['file_path']).st_atime

            if date_choice == "keep":
                final_mtime = keep_mtime
                final_atime = keep_atime
            else:  # other
                final_mtime = other_mtime
                final_atime = other_atime

            final_path = os.path.join(final_dir, final_filename)

            # Show confirmation (unless skip confirmations is enabled)
            if not self.skip_confirmations.get():
                # Check if extension was changed
                chosen_filename_without_correction = os.path.basename(
                    keep_file['file_path'] if filename_choice == "keep" else other_file['file_path']
                )
                extension_note = ""
                if os.path.splitext(chosen_filename_without_correction)[1] != base_ext:
                    extension_note = f"\n[WARNING] Extension adjusted to {base_ext} (matches content source)"

                msg = (f"Create hybrid file?\n\n"
                       f"Base Content: {os.path.basename(base_file['file_path'])}\n"
                       f"Final Location: {final_dir}\n"
                       f"Final Name: {final_filename}{extension_note}\n"
                       f"Final Date: {datetime.fromtimestamp(final_mtime).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                       f"The unused file will be moved to duplicates folder.\n"
                       f"This operation can be undone.")

                if not messagebox.askyesno("Confirm Custom Merge", msg):
                    return

            # Get duplicates folder
            if not self.config or not hasattr(self.config, 'duplicate_directory'):
                messagebox.showerror("Configuration Error", "Duplicate directory not configured.")
                return

            duplicates_folder = self.config.duplicate_directory
            os.makedirs(duplicates_folder, exist_ok=True)

            self.log_message(f"Custom merge starting...")
            self.log_message(f"  Base: {os.path.basename(base_file['file_path'])}")
            self.log_message(f"  Final: {final_filename} in {final_dir}")

            # Move base file to final location if needed
            base_path = base_file['file_path']

            if base_path != final_path:
                # Need to move base file to final location
                if os.path.exists(final_path):
                    # Final path exists (probably the other file is there)
                    # Move it to duplicates first
                    discard_name = os.path.basename(final_path)
                    discard_dest = os.path.join(duplicates_folder, discard_name)
                    counter = 1
                    while os.path.exists(discard_dest):
                        name, ext = os.path.splitext(discard_name)
                        discard_dest = os.path.join(duplicates_folder, f"{name}_{counter}{ext}")
                        counter += 1

                    self.status_text.set("Moving discarded file...")
                    shutil.move(final_path, discard_dest)
                    self.log_message(f"Moved to duplicates: {os.path.basename(discard_dest)}")

                # Move base to final location
                self.status_text.set("Moving base file to final location...")
                shutil.move(base_path, final_path)
                self.log_message(f"Moved base to: {final_path}")

            # Move the unused file to duplicates if it still exists
            discard_path = discard_file['file_path']
            if os.path.exists(discard_path) and discard_path != final_path:
                discard_name = os.path.basename(discard_path)
                discard_dest = os.path.join(duplicates_folder, discard_name)
                counter = 1
                while os.path.exists(discard_dest):
                    name, ext = os.path.splitext(discard_name)
                    discard_dest = os.path.join(duplicates_folder, f"{name}_{counter}{ext}")
                    counter += 1

                shutil.move(discard_path, discard_dest)
                self.log_message(f"Moved unused file to duplicates: {os.path.basename(discard_dest)}")

            try:
                final_ctime = os.stat(
                    keep_file['file_path'] if date_choice == "keep" else other_file['file_path']
                ).st_ctime
            except Exception:
                final_ctime = final_mtime

            # Preserve the selected file's full timestamp information.
            _set_file_timestamps(final_path, final_atime, final_mtime, final_ctime)
            self.log_message(f"Set file timestamps: {datetime.fromtimestamp(final_mtime).strftime('%Y-%m-%d %H:%M:%S')}")

            # Regenerate hashes
            self.status_text.set("Regenerating hashes...")
            new_perceptual = self.generate_perceptual_hash(final_path)
            new_md5 = self.generate_md5_hash(final_path)

            # Update database
            # Remove old entries
            self.remove_file_from_database(keep_file)
            if not self._files_equal(keep_file, other_file):
                self.remove_file_from_database(other_file)

            # Add new entry
            self.update_replaced_file_in_database(
                final_table,
                base_file['file_path'],
                final_path,
                final_filename,
                new_perceptual,
                new_md5
            )

            self.log_message(f"[OK] Custom merge complete: {final_filename}")
            self.status_text.set("Ready")

            if not self.skip_confirmations.get():
                messagebox.showinfo("Success", f"Hybrid file created successfully!\n\n{final_path}")

            # Remove processed group
            if self.duplicate_groups and self.current_group_index < len(self.duplicate_groups):
                self.duplicate_groups.pop(self.current_group_index)

                if self.current_group_index >= len(self.duplicate_groups):
                    self.current_group_index = max(0, len(self.duplicate_groups) - 1)

                if self.duplicate_groups:
                    self.display_current_group()
                else:
                    for widget in self.scrollable_frame.winfo_children():
                        widget.destroy()
                    self.status_text.set("All duplicates processed")

                self.update_navigation_state()
                self.total_duplicates.set(len(self.duplicate_groups))

        except Exception as e:
            logger.error(f"Custom merge failed: {e}")
            self.log_message(f"[ERROR] Custom merge failed: {e}")
            messagebox.showerror("Error", f"Custom merge failed:\n{e}")
            self.status_text.set("Ready")

    def update_replaced_file_in_database(self, table_name: str, old_path: str, new_path: str,
                                         new_filename: str, perceptual_hash: Optional[str], md5_hash: Optional[str]):
        """Update file path, name, and all hashes in database after replacing keep file."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            # Normalize paths
            old_path_normalized = old_path.replace('\\', '/')
            new_path_normalized = new_path.replace('\\', '/')

            # Get file size
            file_size = 0
            if os.path.exists(new_path):
                file_size = os.stat(new_path).st_size

            # Check if updated_at column exists
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'updated_at'")
            has_updated_at = cursor.fetchone() is not None

            # Build update query with all fields
            if has_updated_at:
                # Update with new separate hash columns
                query = f"""
                UPDATE `{table_name}`
                SET file_path = %s,
                    file_name = %s,
                    md5_hash = %s,
                    file_size = %s,
                    updated_at = NOW()
                WHERE file_path = %s
                """
            else:
                # Update with new separate hash columns
                # Note: perceptual_hash should be in composite format (avg|dhash|phash|whash)
                # and will be split by database_manager
                query = f"""
                UPDATE `{table_name}`
                SET file_path = %s,
                    file_name = %s,
                    md5_hash = %s,
                    file_size = %s
                WHERE file_path = %s
                """

            cursor.execute(query, (
                new_path_normalized,
                new_filename,
                md5_hash or '',
                file_size,
                old_path_normalized
            ))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Updated replaced file in database: {old_path} -> {new_path}")
            logger.info(f"  Perceptual hash: {perceptual_hash[:16] if perceptual_hash else 'None'}...")
            logger.info(f"  MD5 hash: {md5_hash[:16] if md5_hash else 'None'}...")

        except Exception as e:
            logger.error(f"Failed to update replaced file in database: {e}")
            raise

    def add_file_to_duplicates_database(self, file_info: Dict, new_path: str):
        """Add a file entry to the duplicates table or appropriate table in database."""
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            # Normalize path
            normalized_path = new_path.replace('\\', '/')

            # Use original table or duplicates table
            table_name = file_info['table_name']
            file_name = os.path.basename(new_path)
            # Use md5_hash specifically, not the perceptual hash_value
            md5_value = file_info.get('md5_hash', '')

            # Get file stats
            if os.path.exists(new_path):
                stat = os.stat(new_path)
                file_size = stat.st_size
            else:
                file_size = 0

            # Check which timestamp columns exist
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'created_at'")
            has_created_at = cursor.fetchone() is not None
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'updated_at'")
            has_updated_at = cursor.fetchone() is not None

            # Build insert query based on available columns
            # Note: We don't restore perceptual hashes here - they remain in separate columns unchanged
            if has_created_at and has_updated_at:
                query = f"""
                INSERT INTO `{table_name}`
                (file_path, file_name, md5_hash, file_size, created_at, updated_at)
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                """
            elif has_created_at:
                query = f"""
                INSERT INTO `{table_name}`
                (file_path, file_name, md5_hash, file_size, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                """
            else:
                query = f"""
                INSERT INTO `{table_name}`
                (file_path, file_name, md5_hash, file_size)
                VALUES (%s, %s, %s, %s)
                """

            cursor.execute(query, (normalized_path, file_name, md5_value, file_size))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Added file to database: {new_path}")

        except Exception as e:
            logger.error(f"Failed to add file to database: {e}")
            # Non-fatal, log but don't raise

    def _edit_post_id(self, file_info: Dict):
        """Open dialog to edit e621_post_id for a file."""
        from tkinter import simpledialog

        current_post_id = file_info.get('e621_post_id', '')
        new_post_id = simpledialog.askstring(
            "Edit e621 Post ID",
            f"File: {os.path.basename(file_info['file_path'])}\n\nEnter new e621 post ID (or leave empty for NULL):",
            initialvalue=str(current_post_id) if current_post_id else ""
        )

        if new_post_id is None:
            return  # User cancelled

        # Validate input
        if new_post_id.strip() == "":
            new_post_id_value = None
        else:
            try:
                new_post_id_value = int(new_post_id.strip())
            except ValueError:
                messagebox.showerror("Invalid Input", "Post ID must be a valid integer or empty for NULL")
                return

        # Update database
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            table_name = file_info['table_name']
            file_path = file_info['file_path']

            # Normalize path
            normalized_path = os.path.normpath(file_path).replace('\\', '/')

            cursor.execute(f"""
                UPDATE `{table_name}`
                SET e621_post_id = %s
                WHERE file_path = %s
            """, (new_post_id_value, normalized_path))

            conn.commit()
            cursor.close()
            conn.close()

            # Update the file_info dict
            file_info['e621_post_id'] = new_post_id_value

            messagebox.showinfo("Success", f"Updated e621_post_id to {new_post_id_value}")

            # Refresh the display
            self.refresh_current_group()

        except Exception as e:
            logger.error(f"Failed to update post ID: {e}")
            messagebox.showerror("Error", f"Failed to update post ID: {e}")

    def _edit_filename(self, file_info: Dict):
        """Open dialog to edit filename for a file."""
        from tkinter import simpledialog

        current_path = file_info['file_path']
        current_filename = os.path.basename(current_path)
        current_name, current_ext = os.path.splitext(current_filename)

        new_filename = simpledialog.askstring(
            "Edit Filename",
            f"Current: {current_filename}\n\nEnter new filename (without extension):",
            initialvalue=current_name
        )

        if not new_filename or new_filename.strip() == "":
            return  # User cancelled or empty

        new_filename = new_filename.strip()

        # Add back the extension
        new_full_filename = new_filename + current_ext
        new_path = os.path.join(os.path.dirname(current_path), new_full_filename)

        # Check if new name already exists
        if os.path.exists(new_path) and os.path.normpath(new_path) != os.path.normpath(current_path):
            if not messagebox.askyesno("File Exists", f"File '{new_full_filename}' already exists. Overwrite?"):
                return

        # Rename the file
        try:
            os.rename(current_path, new_path)

            # Update database
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()

            table_name = file_info['table_name']
            normalized_old_path = os.path.normpath(current_path).replace('\\', '/')
            normalized_new_path = os.path.normpath(new_path).replace('\\', '/')

            cursor.execute(f"""
                UPDATE `{table_name}`
                SET file_path = %s, file_name = %s
                WHERE file_path = %s
            """, (normalized_new_path, new_full_filename, normalized_old_path))

            conn.commit()
            cursor.close()
            conn.close()

            # Update the file_info dict
            file_info['file_path'] = new_path

            messagebox.showinfo("Success", f"Renamed to {new_full_filename}")

            # Refresh the display
            self.refresh_current_group()

        except Exception as e:
            logger.error(f"Failed to rename file: {e}")
            messagebox.showerror("Error", f"Failed to rename file: {e}")

    def refresh_current_group(self):
        """Refresh the current duplicate group display."""
        if self.duplicate_groups and 0 <= self.current_group_index < len(self.duplicate_groups):
            current_group = self.duplicate_groups[self.current_group_index]
            self.display_comparison_view(current_group)

    def log_message(self, message: str):
        """Add message to the operation log."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_entry = f"[{timestamp}] {message}\n"

        self.log_text.insert(tk.END, log_entry)
        self.log_text.see(tk.END)

        # Limit log size
        lines = self.log_text.get(1.0, tk.END).count('\n')
        if lines > 1000:
            self.log_text.delete(1.0, f"{lines - 800}.0")


def main(argv=None):  # fff: argv is ignored (no options); lets the CLI call it uniformly
    """Main entry point."""
    root = tk.Tk()

    try:
        app = DuplicateManagerGUI(root)
        root.mainloop()
    except Exception as e:
        messagebox.showerror("Application Error", f"Failed to start application:\n{e}")


if __name__ == "__main__":
    main()