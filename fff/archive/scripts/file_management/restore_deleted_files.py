# fff: the hard-coded CSV/search/duplicates/not-found paths are argparse options defaulting
# fff: to the original values; argparse replaces the sys.argv flag check.
import argparse
import csv
import shutil
from pathlib import Path

# fff: hard-coded paths became options (defaults are the original values)
DEFAULT_CSV_FILE = r"d:\Code Projects\e621 archive\deleted_files_20251111.csv"
DEFAULT_SEARCH_ROOT = r"P:\Testing"
DEFAULT_DUPLICATES_DIR = r"P:\Test\Duplicates"
DEFAULT_NOT_FOUND_CSV = r"d:\Code Projects\e621 archive\not_found_files.csv"

def find_and_copy_files(check_only=False, csv_file=DEFAULT_CSV_FILE, search_root=DEFAULT_SEARCH_ROOT,
                        duplicates_dir=DEFAULT_DUPLICATES_DIR, not_found_csv=DEFAULT_NOT_FOUND_CSV):
    r"""
    Read deleted_files CSV, search for files in P:\Testing\,
    and restore them to their original locations.
    If a file already exists, keep the older one and move the newer to P:\Test\Duplicates
    """
    search_root = Path(search_root)  # fff: option
    duplicates_dir = Path(duplicates_dir)  # fff: option

    # Create duplicates directory if it doesn't exist
    duplicates_dir.mkdir(parents=True, exist_ok=True)

    # Statistics
    found_count = 0
    not_found_count = 0
    copied_count = 0
    duplicate_count = 0
    error_count = 0
    not_found_files = []  # Track files that weren't found

    # Read CSV file
    print(f"Reading CSV file: {csv_file}")
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total_files = len(rows)
    print(f"Total files to search for: {total_files}\n")

    if check_only:
        print("*** CHECK ONLY MODE - No files will be copied ***\n")

    for idx, row in enumerate(rows, 1):
        post_id = row['PostID']
        original_path = Path(row['FilePath'])
        file_name = row['FileName']

        print(f"[{idx}/{total_files}] Searching for: {file_name}")
        print(f"  Original location: {original_path}")

        # Search for file in P:\Testing\ and subdirectories
        found_file = None
        try:
            for file_path in search_root.rglob(file_name):
                if file_path.is_file():
                    found_file = file_path
                    print(f"  [OK] Found at: {file_path}")
                    found_count += 1
                    break
        except Exception as e:
            print(f"  [ERROR] Error searching: {e}")
            error_count += 1
            continue

        if found_file:
            # Restore file to original location
            if check_only:
                # In check-only mode, just note it was found
                print(f"  [OK] Would restore to: {original_path}")
            else:
                try:
                    # Create parent directory if it doesn't exist
                    original_path.parent.mkdir(parents=True, exist_ok=True)

                    # Check if file already exists at destination
                    if original_path.exists():
                        print("  [WARNING] File already exists at destination")

                        # Get modification times
                        found_mtime = found_file.stat().st_mtime
                        existing_mtime = original_path.stat().st_mtime

                        if found_mtime < existing_mtime:
                            # Found file is older - keep it, move existing to duplicates
                            print("  -> Found file is OLDER (keeping it)")
                            temp_existing = duplicates_dir / file_name

                            # Handle duplicate names in duplicates folder
                            if temp_existing.exists():
                                base = temp_existing.stem
                                ext = temp_existing.suffix
                                counter = 1
                                while temp_existing.exists():
                                    temp_existing = duplicates_dir / f"{base}_{counter}{ext}"
                                    counter += 1

                            shutil.move(str(original_path), str(temp_existing))
                            print(f"  -> Moved existing (newer) to: {temp_existing}")
                            shutil.copy2(found_file, original_path)
                            print(f"  [OK] Restored older file to: {original_path}")
                            copied_count += 1
                            duplicate_count += 1
                        else:
                            # Existing file is older - keep it, move found to duplicates
                            print("  -> Existing file is OLDER (keeping it)")
                            temp_found = duplicates_dir / file_name

                            # Handle duplicate names in duplicates folder
                            if temp_found.exists():
                                base = temp_found.stem
                                ext = temp_found.suffix
                                counter = 1
                                while temp_found.exists():
                                    temp_found = duplicates_dir / f"{base}_{counter}{ext}"
                                    counter += 1

                            shutil.copy2(found_file, temp_found)
                            print(f"  -> Moved found (newer) to: {temp_found}")
                            duplicate_count += 1
                    else:
                        # No existing file - just copy
                        shutil.copy2(found_file, original_path)
                        print(f"  [OK] Restored to: {original_path}")
                        copied_count += 1

                except Exception as e:
                    print(f"  [ERROR] Error copying: {e}")
                    error_count += 1
        else:
            print(f"  [ERROR] Not found in {search_root}\\")  # fff: was literal P:\\Testing\\
            not_found_count += 1
            not_found_files.append({
                'PostID': post_id,
                'FileName': file_name,
                'OriginalPath': str(original_path)
            })

        print()

    # Print summary
    print("=" * 70)
    print("SUMMARY:")
    print(f"  Total files processed: {total_files}")
    print(f"  Files found: {found_count}")
    print(f"  Files not found: {not_found_count}")
    print(f"  Files restored: {copied_count}")
    print(f"  Duplicates handled: {duplicate_count}")
    print(f"  Errors: {error_count}")
    print("=" * 70)

    # Print not found files
    if not_found_files:
        print("\nFILES NOT FOUND:")
        print("=" * 70)
        for i, file_info in enumerate(not_found_files, 1):
            print(f"{i}. Post ID: {file_info['PostID']}")
            print(f"   File: {file_info['FileName']}")
            print(f"   Expected at: {file_info['OriginalPath']}")
            print()

        # Save to file
        # fff: not_found_csv is an option
        with open(not_found_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['PostID', 'FileName', 'OriginalPath'])
            writer.writeheader()
            writer.writerows(not_found_files)
        print(f"Not found files saved to: {not_found_csv}")
        print("=" * 70)

def main(argv=None):
    """fff: argparse replaces the original sys.argv check (same --check-only / -c flag)."""
    parser = argparse.ArgumentParser(description="Restore deleted files listed in a CSV from a search folder")
    parser.add_argument("--check-only", "-c", action="store_true", help="Only report, do not copy files")
    parser.add_argument("--csv-file", default=DEFAULT_CSV_FILE, help="CSV of deleted files (PostID, FilePath, FileName)")
    parser.add_argument("--search-root", default=DEFAULT_SEARCH_ROOT, help="Folder searched recursively for the files")
    parser.add_argument("--duplicates-dir", default=DEFAULT_DUPLICATES_DIR, help="Where the newer copy of a duplicate goes")
    parser.add_argument("--not-found-csv", default=DEFAULT_NOT_FOUND_CSV, help="Where the list of files not found is written")
    args = parser.parse_args(argv)
    check_only = args.check_only

    if check_only:
        print("Running in CHECK ONLY mode\n")

    find_and_copy_files(check_only=check_only, csv_file=args.csv_file, search_root=args.search_root,
                        duplicates_dir=args.duplicates_dir, not_found_csv=args.not_found_csv)

if __name__ == "__main__":
    main()
