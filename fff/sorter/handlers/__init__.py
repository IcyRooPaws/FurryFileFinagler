"""
FFF Handlers Package

This package contains all the core handler modules for the FFF project.
"""

# Import all handler classes for easy access
from .utils import Config, setup_logging
from .database_manager import DatabaseManager
from .file_operations import FileOperations
from .file_sorter import FileSorter
from .hash_manager import HashManager
from .artist_manager import ArtistManager
from .archive_manager import ArchiveManager

__all__ = [
    'Config',
    'setup_logging',
    'DatabaseManager', 
    'FileOperations',
    'FileSorter',
    'HashManager',
    'ArtistManager',
    'ArchiveManager'
]
