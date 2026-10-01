# The sorter exposes one shared Config implementation.
# fff: settings come from the unified config's sorter view; tag_sorting.yaml / sort_artists.yaml
# fff: live in config/; the e621 database name comes from the environment.
"""
Configuration module for FFF.

Provides unified configuration management with validation and easy access to settings.
"""
import os
import logging
import yaml
from typing import Dict, List, Optional

from fff.common import config as fff_config
from fff.sorter import paths as sorter_paths

DEFAULT_CONFIG_FILE = "configs/config.yaml"  # the existing code's CWD-relative default


class Config:
    """Centralized configuration manager for FFF."""

    def __init__(self, config_file: str = "configs/config.yaml"):
        """Initialize configuration from YAML file."""
        self.config_file = config_file
        self.config = self._load_config()
        self._validate_config()
        self._setup_properties()

    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        # fff: the default path (or none) means the sorter view of config/config.yaml; an
        # explicit path still loads an FFF-format file, as the original did.
        if self.config_file in (None, DEFAULT_CONFIG_FILE):
            return fff_config.legacy_view("sorter")
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(
                f"Configuration file '{self.config_file}' not found. "
                "Please create it from config.yaml.example"
            )

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in configuration file: {e}")
        except Exception as e:
            raise RuntimeError(f"Error reading configuration file: {e}")

    def _validate_config(self) -> None:
        """Validate required configuration sections."""
        required_sections = ['SortMobileDownloadFolder', 'destination_directories', 'other', 'mysql']
        for section in required_sections:
            if section not in self.config:
                raise ValueError(f"Missing required configuration section: {section}")

    def _load_tag_sorting_config(self) -> Dict:
        """Load tag sorting configuration from tag_sorting.yaml."""
        tag_sorting_file = str(sorter_paths.TAG_SORTING)  # fff: was CWD-relative configs/tag_sorting.yaml

        # Try to load from separate file first
        if os.path.exists(tag_sorting_file):
            try:
                # fff: target folders are rerouted under the test root in the test environment
                config = sorter_paths.load_yaml_for_env(tag_sorting_file)
                return config if config else {}
            except yaml.YAMLError as e:
                print(f"Warning: Invalid YAML in tag_sorting.yaml: {e}")
                return {}
            except Exception as e:
                print(f"Warning: Error reading tag_sorting.yaml: {e}")
                return {}

        # Fall back to config.yaml for backward compatibility
        return self.config.get("tag_sorting", {})

    def _setup_properties(self) -> None:
        """Setup configuration properties from loaded config."""
        try:
            # Source directories
            sort_section = self.config["SortMobileDownloadFolder"]
            raw_source_dirs = sort_section["source_directories"]
            self.source_directories = [d.strip() for d in raw_source_dirs.splitlines() if d.strip()]
            self.scan_subdirectories = bool(sort_section.get("scan_subdirectories", True))

            # Destination directories
            self.destination_directories = self.config["destination_directories"]

            # Archive directories (optional)
            self.archive_directories = self.config.get("archive_directories", {})
            self.archive_options = self.config.get("archive_options", {})
            self.ignore_archive_premium = bool(self.archive_options.get("ignore_premium_directory", False))

            # Other directories
            other_config = self.config["other"]
            self.duplicate_directory = other_config["duplicate_directory"]
            self.fav_directory = other_config["fav_directory"]
            self.deleted_from_e621_directory = other_config.get("deleted_from_e621_directory")
            self.archived_favorites_directory = other_config.get("archived_favorites_directory")
            self.move_deleted_posts = other_config.get("move_deleted_posts", True)  # Default to True for backward compatibility
            self.manual_sort_directory = other_config["manual_sort_directory"]
            self.manual_sort_video_directory = other_config["manual_sort_video_directory"]
            self.manual_sort_gif_directory = other_config.get("manual_sort_gif_directory")
            self.possible_missing_files_directory = other_config.get("possible_missing_files_directory")
            self.artist_directory = other_config.get("artist_directory")
            raw_overall_dirs = other_config.get("other_directories_to_hash", [])
            if isinstance(raw_overall_dirs, str):
                self.other_directories_to_hash = [d.strip() for d in raw_overall_dirs.splitlines() if d.strip()]
            else:
                self.other_directories_to_hash = raw_overall_dirs or []
            # Optional: directories that should be ignored by tag/artist sorting
            ignore_dirs = other_config.get("sorting_ignore_directories", [])
            if isinstance(ignore_dirs, str):
                self.sorting_ignore_directories = [ignore_dirs] if ignore_dirs else []
            else:
                self.sorting_ignore_directories = ignore_dirs or []
            # Optional: let pool-based sorting search sorting_ignore_directories anyway
            # (e.g. a "- Comics & Image Sets" favorites folder that tag sorting should
            # leave alone, but pools should still be able to pull matching files from)
            self.pool_sort_search_ignored_directories = bool(
                other_config.get("pool_sort_search_ignored_directories", False)
            )

            self.log_directory = other_config["log_directory"]

            # Reuse the shared sorter Config implementation.
            log_level_str = other_config.get("log_level", "WARNING")
            self.log_level = getattr(logging, log_level_str.upper(), logging.WARNING)

            # MySQL configuration
            mysql_config = self.config["mysql"]
            self.mysql_host = mysql_config["host"]
            self.mysql_port = mysql_config.get("port", 3306)
            self.mysql_user = mysql_config["user"]
            self.mysql_password = mysql_config["password"]
            self.mysql_database = mysql_config["database"]

            # Legacy compatibility properties (for old handlers)
            self.MYSQL_HOST = self.mysql_host
            self.MYSQL_PORT = self.mysql_port
            self.MYSQL_USER = self.mysql_user
            self.MYSQL_PASSWORD = self.mysql_password
            self.MYSQL_DATABASE = self.mysql_database

            # Tag sorting configuration (from separate file)
            self.tag_sorting_config = self._load_tag_sorting_config()

            # Duplicate manager configuration (optional)
            self.duplicate_manager_config = self.config.get("duplicate_manager", {})

            # Artist processing configuration (optional)
            artist_proc_config = self.config.get("artist_processing", {})
            self.skip_archives_in_artist_scan = bool(artist_proc_config.get("skip_archives", True))
            self.artist_check_cache_days = int(artist_proc_config.get("artist_check_cache_days", 7))
            self.force_filesystem_artist_scan = bool(artist_proc_config.get("force_filesystem_scan", False))

        except KeyError as e:
            raise ValueError(f"Missing required configuration key: {e}")
        except Exception as e:
            raise RuntimeError(f"Error processing configuration: {e}")

    def get_mysql_config(self) -> Dict[str, any]:
        """Get MySQL connection configuration."""
        return {
            'host': self.mysql_host,
            'port': self.mysql_port,
            'user': self.mysql_user,
            'password': self.mysql_password,
            'database': self.mysql_database
        }

    def get_tag_sorting_config(self) -> Dict:
        """Get tag sorting configuration."""
        return self.tag_sorting_config

    def get_duplicate_manager_config(self) -> Dict:
        """Get duplicate manager configuration."""
        return self.duplicate_manager_config

    def should_ignore_archive_premium(self) -> bool:
        """Return True when archive_premium operations should be skipped."""
        return bool(getattr(self, "ignore_archive_premium", False))

    def get_directory_priorities(self) -> List[Dict]:
        """Get directory priority patterns for duplicate manager."""
        return self.duplicate_manager_config.get("directory_priority", [])

    def get_filename_priorities(self) -> List[Dict]:
        """Get filename priority patterns for duplicate manager."""
        return self.duplicate_manager_config.get("filename_priority", [])

    def is_tag_sorting_enabled(self) -> bool:
        """Check if tag sorting is enabled."""
        return self.tag_sorting_config.get('enabled', False)

    def get_tag_sorting_rules(self) -> List[Dict]:
        """Get list of tag sorting rules."""
        if not self.is_tag_sorting_enabled():
            return []

        rules = self.tag_sorting_config.get('rules', [])
        if not rules:
            return []

        # Filter only enabled rules
        return [rule for rule in rules if rule.get('enabled', True)]

    def get_tag_sorting_base_dirs(self) -> Dict[str, str]:
        """Get base directories for tag sorting outputs (pictures/videos/gifs).

        Priority:
        1. Explicit ``base_directories`` block in tag_sorting.yaml
        2. config.yaml ``destination_directories`` + ``tag_sort_subdirectory``
        3. Empty strings (no split-mode routing possible)
        """
        import os
        bases = self.tag_sorting_config.get('base_directories', {}) or {}
        if any(bases.get(k, '') for k in ('pictures', 'videos', 'gifs')):
            return {
                'pictures': bases.get('pictures', ''),
                'videos': bases.get('videos', ''),
                'gifs': bases.get('gifs', ''),
            }
        # Derive automatically from config.yaml destination_directories
        subdir = self.tag_sorting_config.get('tag_sort_subdirectory', '') or ''
        dest = self.destination_directories or {}
        def _join(base: str) -> str:
            if not base:
                return ''
            return os.path.join(base, subdir) if subdir else base
        return {
            'pictures': _join(dest.get('nsfw_image', '')),
            'videos': _join(dest.get('nsfw_video', '')),
            'gifs': _join(dest.get('nsfw_gif', '')),
        }

    def get_e621_database_config(self) -> Dict[str, any]:
        """Get e621 database configuration (falls back to main MySQL config)."""
        e621_db = self.tag_sorting_config.get('e621_database', {})

        return {
            'host': e621_db.get('host') or self.mysql_host,
            'port': e621_db.get('port') or self.mysql_port,
            'user': e621_db.get('user') or self.mysql_user,
            'password': e621_db.get('password') or self.mysql_password,
            # fff: the environment decides the schema (prod or test_), not tag_sorting.yaml
            'database': sorter_paths.db_names()['archive']
        }

    def get_all_destination_dirs(self) -> List[str]:
        """Get all destination directories as a list."""
        return list(self.destination_directories.values())

    def reload(self) -> None:
        """Reload configuration from file."""
        self.config = self._load_config()
        self._validate_config()
        self._setup_properties()


def ensure_directory_exists(directory: str) -> None:
    """Create directory if it doesn't exist."""
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")


def load_artists_config(artists_file: str = str(sorter_paths.SORT_ARTISTS)) -> Dict:
    """Load artists configuration from YAML file."""
    if not os.path.exists(artists_file):
        print(f"Warning: Artists file '{artists_file}' not found. Creating empty config.")
        return {}

    try:
        with open(artists_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        print(f"Warning: Invalid YAML in artists file: {e}")
        return {}
    except Exception as e:
        print(f"Warning: Error reading artists file: {e}")
        return {}
