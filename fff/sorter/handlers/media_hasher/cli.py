#!/usr/bin/env python3
# fff: old v1 hasher package, kept verbatim: DatabaseManager._get_hash_metadata uses its type
# fff: mapping for the hash_method/hash_type columns, and 'fff hash' wraps its CLI.
"""
Command-line interface for MediaHasher
Provides easy access to hashing functionality from the command line
"""

import argparse
import sys
import json
from pathlib import Path
from typing import Optional

from .core import MediaHasher, HAS_IMAGE_SUPPORT
from .utils import (
    batch_hash_directory, 
    find_duplicates, 
    create_hash_report,
    supported_extensions
)


def hash_command(args):
    """Handle the hash subcommand"""
    hasher = MediaHasher(hash_length=args.length)
    
    if args.file:
        # Hash single file
        result = hasher.hash_file(args.file)
        if result:
            if args.json:
                output = {"file": str(args.file), "hash": result}
                print(json.dumps(output, indent=2))
            else:
                print(f"{args.file}: {result}")
        else:
            print(f"Error: Could not hash {args.file}", file=sys.stderr)
            sys.exit(1)
    
    elif args.directory:
        # Hash directory
        try:
            results = batch_hash_directory(
                args.directory, 
                recursive=args.recursive,
                hash_length=args.length
            )
            
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                for file_path, file_hash in results.items():
                    status = file_hash if file_hash else "ERROR"
                    print(f"{file_path}: {status}")
        
        except Exception as e:
            print(f"Error processing directory: {e}", file=sys.stderr)
            sys.exit(1)


def info_command(args):
    """Handle the info subcommand"""
    hasher = MediaHasher()
    info = hasher.get_file_info(args.file)
    
    if args.json:
        print(json.dumps(info, indent=2, default=str))
    else:
        print(f"File: {info.get('name', 'Unknown')}")
        print(f"Path: {info.get('path', 'Unknown')}")
        print(f"Type: {info.get('file_type', 'Unknown')}")
        print(f"Size: {info.get('size', 'Unknown')} bytes")
        print(f"Hash: {info.get('hash', 'None')}")
        print(f"Method: {info.get('hash_method', 'Unknown')}")
        if 'error' in info:
            print(f"Error: {info['error']}")


def duplicates_command(args):
    """Handle the duplicates subcommand"""
    try:
        results = batch_hash_directory(
            args.directory,
            recursive=args.recursive,
            hash_length=args.length
        )
        
        duplicates = find_duplicates(results)
        
        if args.json:
            print(json.dumps(duplicates, indent=2))
        else:
            if not duplicates:
                print("No duplicates found.")
            else:
                print(f"Found {len(duplicates)} groups of duplicates:")
                for hash_value, files in duplicates.items():
                    print(f"\nHash {hash_value}:")
                    for file_path in files:
                        print(f"  - {file_path}")
    
    except Exception as e:
        print(f"Error finding duplicates: {e}", file=sys.stderr)
        sys.exit(1)


def report_command(args):
    """Handle the report subcommand"""
    try:
        report = create_hash_report(
            args.directory,
            output_file=args.output,
            recursive=args.recursive,
            hash_length=args.length
        )
        
        if not args.output:
            print(report)
        else:
            print(f"Report saved to: {args.output}")
    
    except Exception as e:
        print(f"Error creating report: {e}", file=sys.stderr)
        sys.exit(1)


def extensions_command(args):
    """Handle the extensions subcommand"""
    extensions = supported_extensions()
    
    if args.json:
        # Convert sets to lists for JSON serialization
        json_extensions = {k: list(v) for k, v in extensions.items()}
        print(json.dumps(json_extensions, indent=2))
    else:
        for file_type, exts in extensions.items():
            print(f"{file_type.title()}: {', '.join(sorted(exts))}")


def version_command(args):
    """Handle the version subcommand"""
    from . import __version__
    print(f"MediaHasher version {__version__}")
    print(f"Image support: {'Yes' if HAS_IMAGE_SUPPORT else 'No (install with: pip install media-hasher[image])'}")


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="MediaHasher - Multi-format file hashing utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s hash file.jpg                    # Hash a single file
  %(prog)s hash -d /path/to/photos         # Hash all files in directory
  %(prog)s info file.jpg                   # Get detailed file info
  %(prog)s duplicates /path/to/photos      # Find duplicate files
  %(prog)s report /path/to/photos          # Generate comprehensive report
  %(prog)s extensions                      # Show supported file types
        """
    )
    
    # Global options
    parser.add_argument(
        "--json", 
        action="store_true", 
        help="Output results in JSON format"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Hash command
    hash_parser = subparsers.add_parser("hash", help="Hash files or directories")
    hash_group = hash_parser.add_mutually_exclusive_group(required=True)
    hash_group.add_argument("file", nargs="?", help="File to hash")
    hash_group.add_argument("-d", "--directory", help="Directory to hash")
    hash_parser.add_argument("-l", "--length", type=int, default=16, help="Hash length (default: 16)")
    hash_parser.add_argument("-r", "--recursive", action="store_true", help="Recursive directory scan")
    hash_parser.set_defaults(func=hash_command)
    
    # Info command
    info_parser = subparsers.add_parser("info", help="Get detailed file information")
    info_parser.add_argument("file", help="File to analyze")
    info_parser.set_defaults(func=info_command)
    
    # Duplicates command
    dup_parser = subparsers.add_parser("duplicates", help="Find duplicate files")
    dup_parser.add_argument("directory", help="Directory to scan for duplicates")
    dup_parser.add_argument("-l", "--length", type=int, default=16, help="Hash length (default: 16)")
    dup_parser.add_argument("-r", "--recursive", action="store_true", help="Recursive directory scan")
    dup_parser.set_defaults(func=duplicates_command)
    
    # Report command
    report_parser = subparsers.add_parser("report", help="Generate comprehensive hash report")
    report_parser.add_argument("directory", help="Directory to analyze")
    report_parser.add_argument("-o", "--output", help="Output file for report")
    report_parser.add_argument("-l", "--length", type=int, default=16, help="Hash length (default: 16)")
    report_parser.add_argument("-r", "--recursive", action="store_true", help="Recursive directory scan")
    report_parser.set_defaults(func=report_command)
    
    # Extensions command
    ext_parser = subparsers.add_parser("extensions", help="Show supported file extensions")
    ext_parser.set_defaults(func=extensions_command)
    
    # Version command
    ver_parser = subparsers.add_parser("version", help="Show version information")
    ver_parser.set_defaults(func=version_command)
    
    # Parse arguments
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Execute command
    args.func(args)


if __name__ == "__main__":
    main()
