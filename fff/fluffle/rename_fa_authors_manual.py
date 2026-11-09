# fff: the mappings come from config/fa_author_mappings.yaml instead of being hard-coded.
# File discovery and rename helpers are imimplemented in fff.fluffle.rename_fa_authors.
"""
FurAffinity Author Manual Renamer: like 'fff fa rename-authors' but with hand-written
author mappings (config/fa_author_mappings.yaml) instead of the database's.
Run as 'fff fa rename-authors --manual'.
"""

import yaml

from fff.common import paths
from fff.fluffle.rename_fa_authors import (  # noqa: F401
    find_fa_files,
    load_config,
    process_files,
    rename_file_preserve_mtime,
)

MAPPINGS_FILE = paths.CONFIG_DIR / "fa_author_mappings.yaml"


def load_manual_mappings(path=MAPPINGS_FILE):
    """[(author_id, author_name), ...] from the mappings file (empty if it doesn't exist)."""
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [(str(m["author_id"]), str(m["author_name"])) for m in data.get("mappings") or []]


def main(argv=None):  # fff: argv accepted for the CLI; no options
    """Main execution function."""
    print("FurAffinity Author Manual Renamer")
    print("=" * 60)

    # MANUAL AUTHOR MAPPINGS
    # Format: (author_id, author_name)
    # Add your mappings here:
    manual_mappings = load_manual_mappings()  # fff: was a hard-coded list; edit config/fa_author_mappings.yaml
    if not manual_mappings:
        print(f"\nNo mappings in {MAPPINGS_FILE}. Add some (see fa_author_mappings.example.yaml).")
        return

    print("\nManual author mappings configured:")
    for author_id, author_name in manual_mappings:
        print(f"  {author_name} -> {author_id}")

    # Load configuration
    config = load_config()

    # Get the directory to search from config
    search_directory = config.get('furaffinity', {}).get('rename_search_directory',
                                                          'P:/IPL/Pictures')

    print("\nConfiguration:")
    print(f"  Search directory: {search_directory}")

    # Ask user to confirm or override directory
    print("\nPress Enter to use the above directory, or type a new path:")
    user_input = input().strip()
    if user_input:
        search_directory = user_input

    # Find files with -FA- in their name
    print(f"\nSearching for files with '-FA-' in: {search_directory}")
    fa_files = find_fa_files(search_directory)

    if not fa_files:
        print("No files found with '-FA-' in their name. Exiting.")
        return

    # Ask user if they want to do a dry run first
    print(f"\nFound {len(fa_files)} files to process.")
    print("Do you want to do a DRY RUN first? (y/n): ", end='')
    response = input().strip().lower()

    if response in ['y', 'yes', '']:
        print("\n" + "=" * 60)
        print("DRY RUN - No files will be modified")
        print("=" * 60)
        process_files(fa_files, manual_mappings, dry_run=True)

        print("\nDo you want to proceed with actual renaming? (y/n): ", end='')
        response = input().strip().lower()

        if response in ['y', 'yes']:
            print("\n" + "=" * 60)
            print("RENAMING FILES")
            print("=" * 60)
            process_files(fa_files, manual_mappings, dry_run=False)
        else:
            print("Renaming cancelled.")
    else:
        print("\n" + "=" * 60)
        print("RENAMING FILES")
        print("=" * 60)
        process_files(fa_files, manual_mappings, dry_run=False)

    print("\nDone!")


if __name__ == "__main__":
    main()
