#!/usr/bin/env python3
# fff: 'fff sort' entry point: build_parser() (was create_parser) and main(argv=None) -> int.
# fff: --config still names an FFF-format file; its default means the unified config's sorter view.
# advanced-duplicates reports its unsupported database schema when invoked.
"""
FFF - Unified CLI Entry Point

Main command-line interface for the FFF file organization system.
Provides access to all core functionality through subcommands.

Usage:
    python main.py <command> [options]

Commands:
    sort            Sort files by type, rating, and content
    archive         Archive old files to designated location
    status          Show system status and statistics
    maintenance     Run maintenance tasks (cleanup, verification)
    setup           Initial setup and configuration
    post-id         Manage files by e621 post IDs
    tag-sort        Sort files by e621 tags
    pool-sort       Sort files by e621 pool membership
    tag-sort-config Configure tag sorting rules (GUI)
    artist-config   Configure favorite artists (GUI)
    artist-sort     Move favorite artist files to dedicated directory
    cleanup         Delete empty folders in specified directories
    duplicates      Interactive duplicate file management

Examples:
    python main.py sort --dry-run
    python main.py tag-sort --tags yoshi nintendo
    python main.py status
    python main.py duplicates
    python main.py cleanup --directory "P:/Sorted"

For detailed help on any command:
    python main.py <command> --help
"""

import sys
import argparse
import logging

# Import configuration and logging
from fff.common import paths as fff_paths
from fff.sorter.config import Config, DEFAULT_CONFIG_FILE
from fff.sorter.logging_setup import setup_logging

# Import all command classes
from fff.sorter.cli.commands import (
    SortCommand,
    ArchiveCommand,
    UnarchiveCommand,
    StatusCommand,
    MaintenanceCommand,
    SetupCommand,
    PostIDCommand,
    TagSortCommand,
    PoolSortCommand,
    TagSortConfigCommand,
    ArtistConfigCommand,
    ArtistSortCommand,
    CleanupCommand,
    DuplicateManagerCommand
)

# Import advanced duplicates command (optional - requires xxhash)
try:
    from fff.sorter.cli.advanced_duplicates import add_advanced_duplicates_command
    HAS_ADVANCED_DUPLICATES = True
except ImportError as e:
    logger = logging.getLogger('fff.sorter.main')
    logger.debug(f"Advanced duplicates not available (missing dependencies): {e}")
    HAS_ADVANCED_DUPLICATES = False


logger = logging.getLogger('fff.sorter.main')


def build_parser():  # fff: was create_parser
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog='fff sort',
        description='Intelligent media file organization system',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s sort --dry-run              # Preview sorting operations
  %(prog)s tag-sort --tags yoshi       # Sort files with 'yoshi' tag
  %(prog)s status                      # Show system statistics
  %(prog)s duplicates                  # Launch duplicate manager GUI
  %(prog)s cleanup --directory "P:/"   # Clean empty folders

For more information, see the documentation in the docs/ directory.
        """
    )

    parser.add_argument(
        '--config',
        default=DEFAULT_CONFIG_FILE,  # fff: left at default, Config() uses the unified config's sorter view
        help='Path to an FFF-format configuration file (default: the sorter view of config/config.yaml)'
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )

    parser.add_argument(
        '--version',
        action='version',
        version='%(prog)s 2.0.0'
    )

    # Create subparsers for commands
    subparsers = parser.add_subparsers(
        title='commands',
        description='Available commands',
        dest='command',
        help='Command to execute'
    )

    # Sort command
    sort_parser = subparsers.add_parser(
        'sort',
        help='Sort and organize files',
        description='Main file sorting operation - organizes files by type, rating, and content'
    )
    SortCommand.add_arguments(sort_parser)

    # Archive command
    archive_parser = subparsers.add_parser(
        'archive',
        help='Archive old files',
        description='Move old files to archive location based on age'
    )
    ArchiveCommand.add_arguments(archive_parser)

    # Unarchive command
    unarchive_parser = subparsers.add_parser(
        'unarchive',
        help='Restore recent files from archives',
        description='Move recent files from archives back to sorting directory'
    )
    UnarchiveCommand.add_arguments(unarchive_parser)

    # Status command
    status_parser = subparsers.add_parser(
        'status',
        help='Show system status',
        description='Display system statistics, database info, and configuration'
    )
    StatusCommand.add_arguments(status_parser)

    # Maintenance command
    maintenance_parser = subparsers.add_parser(
        'maintenance',
        help='Run maintenance tasks',
        description='Perform system maintenance (cleanup logs, verify database, fix issues)'
    )
    MaintenanceCommand.add_arguments(maintenance_parser)

    # Setup command
    setup_parser = subparsers.add_parser(
        'setup',
        help='Initial setup and configuration',
        description='Set up database, create directories, and verify configuration'
    )
    SetupCommand.add_arguments(setup_parser)

    # Post ID command
    postid_parser = subparsers.add_parser(
        'post-id',
        help='Manage files by e621 post IDs',
        description='Launch GUI for managing files using e621 post IDs'
    )
    PostIDCommand.add_arguments(postid_parser)

    # Tag Sort command
    tagsort_parser = subparsers.add_parser(
        'tag-sort',
        help='Sort files by e621 tags',
        description='Sort files based on tags from e621 database'
    )
    TagSortCommand.add_arguments(tagsort_parser)

    # Pool Sort command
    poolsort_parser = subparsers.add_parser(
        'pool-sort',
        help='Sort files by e621 pool membership',
        description='Sort files based on e621 pool IDs from e621 database'
    )
    PoolSortCommand.add_arguments(poolsort_parser)

    # Artist Sort command
    artistsort_parser = subparsers.add_parser(
        'artist-sort',
        help='Sort files by favorite artists',
        description='Move files from favorite artists to dedicated directory'
    )
    ArtistSortCommand.add_arguments(artistsort_parser)

    # Artist Config command
    artistconfig_parser = subparsers.add_parser(
        'artist-config',
        help='Configure favorite artists (GUI)',
        description='Launch GUI to manage artists.yaml entries'
    )
    ArtistConfigCommand.add_arguments(artistconfig_parser)

    # Cleanup command
    cleanup_parser = subparsers.add_parser(
        'cleanup',
        help='Delete empty folders',
        description='Scan directories and remove empty folders'
    )
    CleanupCommand.add_arguments(cleanup_parser)

    # Tag Sort Config command
    tagsortconfig_parser = subparsers.add_parser(
        'tag-sort-config',
        help='Configure tag sorting rules (GUI)',
        description='Launch GUI to manage tag_sorting.yaml rules'
    )
    TagSortConfigCommand.add_arguments(tagsortconfig_parser)

    # Duplicates command
    duplicates_parser = subparsers.add_parser(
        'duplicates',
        help='Manage duplicate files',
        description='Launch interactive GUI for duplicate file management'
    )
    DuplicateManagerCommand.add_arguments(duplicates_parser)

    # Advanced duplicates command (optional - requires xxhash)
    if HAS_ADVANCED_DUPLICATES:
        add_advanced_duplicates_command(subparsers)

    return parser


def main(argv=None) -> int:
    """Main entry point for the CLI."""
    # Create parser
    parser = build_parser()

    # Parse arguments
    args = parser.parse_args(argv)

    # If no command specified, show help
    if not args.command:
        parser.print_help()
        return 1

    try:
        # Load configuration
        config = Config(args.config)

        # Setup logging
        setup_logging(
            log_directory=config.log_directory,
            level=getattr(logging, args.log_level)
        )

        logger.info(f"Starting FFF - Command: {args.command}")
        logger.debug(f"Configuration loaded from: {args.config}")

        # Map commands to their handler classes
        command_map = {
            'sort': SortCommand,
            'archive': ArchiveCommand,
            'unarchive': UnarchiveCommand,
            'status': StatusCommand,
            'maintenance': MaintenanceCommand,
            'setup': SetupCommand,
            'post-id': PostIDCommand,
            'tag-sort': TagSortCommand,
            'pool-sort': PoolSortCommand,
            'artist-sort': ArtistSortCommand,
            'artist-config': ArtistConfigCommand,
            'cleanup': CleanupCommand,
            'tag-sort-config': TagSortConfigCommand,
            'duplicates': DuplicateManagerCommand,
        }

        # Handle advanced-duplicates separately (uses different interface)
        if args.command == 'advanced-duplicates':
            if not HAS_ADVANCED_DUPLICATES:
                print("Error: Advanced duplicates feature requires additional dependencies.")
                print("Install with: pip install xxhash")
                return 1
            print("WARNING: advanced-duplicates requires an obsolete database schema")
            from fff.sorter.cli.advanced_duplicates import run_advanced_duplicates
            return run_advanced_duplicates(args, config)

        # Execute the appropriate command
        command_class = command_map.get(args.command)
        if command_class:
            return command_class.execute(args, config)
        else:
            logger.error(f"Unknown command: {args.command}")
            print(f"Error: Unknown command '{args.command}'")
            parser.print_help()
            return 1

    except FileNotFoundError as e:
        print(f"Error: Configuration file not found - {e}")
        print(f"Please create a config file at: {args.config}")
        print(f"See: {fff_paths.CONFIG_DIR / 'config.example.yaml'}")  # fff: was configs/config.yaml.example
        return 1

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        print(f"Error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
