# fff: implemented in media sorter/fff.sorter/cli/advanced_duplicates.py.
# fff: ported as is and still broken (decision 2, design note section13); only the imports moved and a warning
# fff: is printed when it runs.
"""
CLI command for advanced duplicate detection

Integrates with main.py command structure
"""

import logging
from pathlib import Path
from typing import Optional, List, Set

from fff.sorter.core.advanced_deduplicator import (
    AdvancedDeduplicator, DedupeMode, DuplicateFile, DuplicateGroup
)
from fff.sorter.config import Config

logger = logging.getLogger('fff.sorter.advanced_duplicates')


def add_advanced_duplicates_command(subparsers):
    """Add the advanced-duplicates command to argument parser"""
    parser = subparsers.add_parser(
        'advanced-duplicates',
        aliases=['adupe'],
        help='Find duplicates using multi-stage pipeline (fast and efficient)'
    )
    
    parser.add_argument(
        '--mode',
        choices=['fast', 'normal', 'full', 'perceptual'],
        default='normal',
        help='Deduplication mode (default: normal)'
    )
    
    parser.add_argument(
        '--tables',
        nargs='+',
        help='Specific database tables to scan'
    )
    
    parser.add_argument(
        '--min-size',
        type=str,
        help='Minimum file size (e.g., 1MB, 500KB)'
    )
    
    parser.add_argument(
        '--max-size',
        type=str,
        help='Maximum file size (e.g., 100MB)'
    )
    
    parser.add_argument(
        '--extensions',
        nargs='+',
        help='File extensions to include'
    )
    
    parser.add_argument(
        '--export',
        type=str,
        help='Export results to CSV file'
    )
    
    parser.set_defaults(func=run_advanced_duplicates)


def run_advanced_duplicates(args):
    """Execute advanced duplicate detection"""
    print("WARNING: advanced-duplicates is known to be broken (project notes §13)")  # fff: decision 2
    from fff.sorter.scripts.find_duplicates_advanced import AdvancedDuplicateFinder, parse_size
    
    # Parse arguments
    min_size = parse_size(args.min_size) if args.min_size else None
    max_size = parse_size(args.max_size) if args.max_size else None
    
    extensions = None
    if args.extensions:
        extensions = {ext if ext.startswith('.') else f'.{ext}' for ext in args.extensions}
    
    mode = DedupeMode[args.mode.upper()]
    
    # Run detection
    finder = AdvancedDuplicateFinder()
    groups, stats = finder.find_duplicates(
        tables=args.tables,
        mode=mode,
        min_size=min_size,
        max_size=max_size,
        extensions=extensions
    )
    
    # Print results
    finder.print_results(groups, stats)
    
    # Export if requested
    if args.export and groups:
        finder.export_to_csv(groups, args.export)
        print(f"\n💾 Results exported to {args.export}")
