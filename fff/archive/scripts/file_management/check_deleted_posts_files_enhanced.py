#!/usr/bin/env python3
# fff: report goes to data/archive/reports/, log to logs/archive/.
# fff: max_posts = 10 is still hard-coded in main() as in the original (only 10 posts are checked).
"""
Enhanced script to check for deleted posts in the database and verify if their files still exist
in the configured download directories.

This improved version:
1. Tries multiple file_name variations (with/without extensions)
2. Falls back to MD5-only search if file_name search fails
3. Provides more detailed logging about search attempts
4. Reports partial matches for debugging
"""

import os
import sys
import logging
from typing import List, Dict, Tuple, Optional
import hashlib
import re

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql


def setup_script_logging():
    """Setup logging specifically for this script."""
    log_dir = str(archive_paths.LOG_DIR)  # fff: was ./logs
    os.makedirs(log_dir, exist_ok=True)

    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"{timestamp}_check_deleted_files_enhanced.log")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    return log_file


def get_deleted_posts_with_details(conn) -> List[Dict]:
    """
    Get all deleted posts from the database with their file information.

    Returns:
        List of dictionaries containing post details
    """
    cursor = conn.cursor()
    try:
        query = """
            SELECT id, file_url, md5, file_name, file_path, deleted_at, deletion_reason, rating
            FROM posts
            WHERE deleted = 1
            ORDER BY deleted_at DESC
        """
        cursor.execute(query)
        results = cursor.fetchall()

        posts = []
        for row in results:
            post_id, file_url, md5, file_name, file_path, deleted_at, deletion_reason, rating = row
            posts.append({
                'id': post_id,
                'file_url': file_url,
                'md5': md5,
                'file_name': file_name,
                'file_path': file_path,
                'deleted_at': deleted_at,
                'deletion_reason': deletion_reason,
                'rating': rating
            })

        return posts
    finally:
        cursor.close()


def get_search_directories(config: Dict) -> List[str]:
    """
    Extract all download directories from the config.

    Returns:
        List of directory paths to search
    """
    directories = []
    download_config = config.get('download', {})

    # Add main directories
    for key, value in download_config.items():
        if key in ['duplicates_directory', 'cache_directory', 'all_star_favs', 'default']:
            if isinstance(value, str):
                directories.append(value)
        elif key in ['images', 'videos', 'gifs'] and isinstance(value, dict):
            # Add subdirectories for each content type
            for subkey, subvalue in value.items():
                if isinstance(subvalue, str):
                    directories.append(subvalue)

    # Remove duplicates and ensure paths exist
    unique_dirs = []
    for directory in directories:
        if directory and directory not in unique_dirs:
            if os.path.exists(directory):
                unique_dirs.append(directory)
            else:
                logging.warning(f"Directory does not exist: {directory}")

    return unique_dirs


def generate_file_name_variations(file_name: str, post_id: int) -> List[str]:
    """
    Generate possible file_name variations to search for.

    Args:
        file_name: Original file_name from database
        post_id: Post ID for generating alternatives

    Returns:
        List of possible file_names to search for
    """
    variations = []

    if file_name:
        # Original file_name
        variations.append(file_name)

        # Add common extensions if not present
        common_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webm', '.mp4', '.webp']
        if not any(file_name.lower().endswith(ext) for ext in common_extensions):
            for ext in common_extensions:
                variations.append(file_name + ext)

        # Remove extension and try with different ones
        name_without_ext = re.sub(r'\.[^.]+$', '', file_name)
        if name_without_ext != file_name:
            variations.append(name_without_ext)
            for ext in common_extensions:
                variations.append(name_without_ext + ext)

    # Generate possible file_names based on post ID
    variations.extend([
        f"{post_id}.jpg",
        f"{post_id}.jpeg",
        f"{post_id}.png",
        f"{post_id}.gif",
        f"{post_id}.webm",
        f"{post_id}.mp4",
        f"{post_id}.webp"
    ])

    # Remove duplicates while preserving order
    seen = set()
    unique_variations = []
    for variation in variations:
        if variation and variation not in seen:
            seen.add(variation)
            unique_variations.append(variation)

    return unique_variations


def find_file_enhanced(directories: List[str], file_name: str, md5_hash: str, post_id: int, enable_md5_search: bool = False) -> Tuple[Optional[str], List[str]]:
    """
    Enhanced search for a file in the given directories.

    Args:
        directories: List of directories to search
        file_name: The file_name to search for
        md5_hash: The MD5 hash to verify against
        post_id: Post ID for generating file_name variations
        enable_md5_search: Whether to perform full MD5 search (slow, disabled by default)

    Returns:
        Tuple of (found_file_path, debug_info_list)
    """
    debug_info = []

    if not file_name and not md5_hash:
        debug_info.append("No file_name or MD5 provided")
        return None, debug_info

    # Generate file_name variations
    file_name_variations = generate_file_name_variations(file_name, post_id)
    debug_info.append(f"Searching for file_name variations: {file_name_variations[:5]}...")  # Show first 5

    for directory in directories:
        debug_info.append(f"Searching in directory: {directory}")

        try:
            # Search by file_name variations first (faster)
            for variation in file_name_variations:
                debug_info.append(f"  Trying file_name: {variation}")
                for root, dirs, files in os.walk(directory):
                    if variation in files:
                        file_path = os.path.join(root, variation)
                        debug_info.append(f"    Found file: {file_path}")

                        # Verify MD5 if provided
                        if md5_hash:
                            if verify_file_md5(file_path, md5_hash):
                                debug_info.append(f"    MD5 verified: {file_path}")
                                return file_path, debug_info
                            else:
                                debug_info.append(f"    MD5 mismatch: {file_path}")
                        else:
                            debug_info.append(f"    No MD5 to verify, accepting: {file_path}")
                            return file_path, debug_info

            # If not found by any file_name variation, search by MD5 (slower, optional)
            if md5_hash and enable_md5_search:
                debug_info.append(f"  file_name search failed, trying MD5 search in {directory}")
                files_checked = 0
                max_files_to_check = 10000  # Limit to prevent freezing on huge directories

                for root, dirs, files in os.walk(directory):
                    for file in files:
                        if files_checked >= max_files_to_check:
                            debug_info.append(f"    Reached max file check limit ({max_files_to_check}), stopping MD5 search")
                            break

                        file_path = os.path.join(root, file)
                        files_checked += 1

                        if files_checked % 100 == 0:
                            msg = f"    Checked {files_checked} files so far..."
                            debug_info.append(msg)
                            logging.debug(msg)

                        if verify_file_md5(file_path, md5_hash):
                            debug_info.append(f"    MD5 match found: {file_path}")
                            return file_path, debug_info

                    if files_checked >= max_files_to_check:
                        break

                debug_info.append(f"  MD5 search complete: checked {files_checked} files")
            elif md5_hash and not enable_md5_search:
                debug_info.append("  MD5 search disabled (enable with enable_md5_search=True)")

        except Exception as e:
            error_msg = f"Error searching in directory {directory}: {e}"
            debug_info.append(error_msg)
            logging.error(error_msg)

    debug_info.append("File not found in any directory")
    return None, debug_info


def verify_file_md5(file_path: str, expected_md5: str) -> bool:
    """
    Verify that a file's MD5 hash matches the expected value.

    Args:
        file_path: Path to the file
        expected_md5: Expected MD5 hash

    Returns:
        True if MD5 matches, False otherwise
    """
    try:
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest().lower() == expected_md5.lower()
    except Exception as e:
        logging.debug(f"Error verifying MD5 for {file_path}: {e}")
        return False


def generate_e621_url(post_id: int, file_url: str) -> str:
    """
    Generate the e621 post URL for a given post.

    Args:
        post_id: The post ID
        file_url: The current file URL (might be e621 post link for deleted posts)

    Returns:
        e621 post URL
    """
    # If file_url is already an e621 post URL, return it
    if file_url and "e621.net/posts/" in file_url:
        return file_url

    # Otherwise, generate the standard post URL
    return f"https://e621.net/posts/{post_id}"


def check_deleted_posts_files_enhanced(config: Dict, max_posts: int = None, verbose_logging: bool = False) -> Dict:
    """
    Enhanced function to check deleted posts and their files.

    Args:
        config: Configuration dictionary
        max_posts: Maximum number of posts to check (for testing)
        verbose_logging: Whether to include detailed debug info in logs

    Returns:
        Dictionary containing the report data
    """
    mysql_config = config.get('mysql', {})
    conn = connect_to_mysql(mysql_config)

    try:
        # Get all deleted posts
        logging.info("Fetching deleted posts from database...")
        deleted_posts = get_deleted_posts_with_details(conn)

        if max_posts:
            deleted_posts = deleted_posts[:max_posts]
            logging.info(f"Limited to first {max_posts} posts for testing")

        logging.info(f"Found {len(deleted_posts)} deleted posts to check")

        if not deleted_posts:
            logging.info("No deleted posts found in database")
            return {
                'total_deleted_posts': 0,
                'files_found': 0,
                'files_missing': 0,
                'missing_posts': [],
                'found_posts': []
            }

        # Get search directories
        search_directories = get_search_directories(config)
        logging.info(f"Searching in {len(search_directories)} directories:")
        for directory in search_directories:
            logging.info(f"  - {directory}")

        # Check each deleted post
        files_found = 0
        missing_posts = []
        found_posts = []

        for i, post in enumerate(deleted_posts, 1):
            post_id = post['id']
            file_name = post['file_name']
            md5_hash = post['md5']
            file_url = post['file_url']

            logging.info(f"Checking post {post_id} ({i}/{len(deleted_posts)})")

            # Enhanced search for the file (MD5 search disabled by default to prevent freezing)
            found_file, debug_info = find_file_enhanced(
                search_directories,
                file_name,
                md5_hash,
                post_id,
                enable_md5_search=False  # Set to True to enable slow MD5 search
            )

            if found_file:
                files_found += 1
                found_post = {
                    'post_id': post_id,
                    'file_name': file_name,
                    'found_path': found_file,
                    'md5': md5_hash
                }
                found_posts.append(found_post)
                logging.info(f"  [OK] File found: {found_file}")

                if verbose_logging:
                    for debug_line in debug_info:
                        logging.debug(f"    {debug_line}")
            else:
                # File is missing
                e621_url = generate_e621_url(post_id, file_url)
                missing_post = {
                    'post_id': post_id,
                    'file_name': file_name,
                    'md5': md5_hash,
                    'file_url': file_url,
                    'e621_url': e621_url,
                    'deleted_at': post['deleted_at'],
                    'deletion_reason': post.get('deletion_reason'),
                    'rating': post['rating'],
                    'debug_info': debug_info if verbose_logging else []
                }
                missing_posts.append(missing_post)
                logging.info(f"  [ERROR] File missing for post {post_id}")

                if verbose_logging:
                    for debug_line in debug_info:
                        logging.debug(f"    {debug_line}")

        files_missing = len(missing_posts)

        report = {
            'total_deleted_posts': len(deleted_posts),
            'files_found': files_found,
            'files_missing': files_missing,
            'missing_posts': missing_posts,
            'found_posts': found_posts,
            'search_directories': search_directories
        }

        return report

    finally:
        conn.close()


def generate_enhanced_report(report: Dict, output_file: str = None) -> str:
    """
    Generate a human-readable report from the enhanced analysis results.

    Args:
        report: Report data dictionary
        output_file: Optional file to save the report to

    Returns:
        Report text
    """
    from datetime import datetime

    report_text = []
    report_text.append("=" * 80)
    report_text.append("ENHANCED DELETED POSTS FILE VERIFICATION REPORT")
    report_text.append("=" * 80)
    report_text.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_text.append("")

    # Summary
    report_text.append("SUMMARY:")
    report_text.append(f"  Total deleted posts: {report['total_deleted_posts']}")
    report_text.append(f"  Files found: {report['files_found']}")
    report_text.append(f"  Files missing: {report['files_missing']}")

    if report['total_deleted_posts'] > 0:
        found_percentage = (report['files_found'] / report['total_deleted_posts']) * 100
        missing_percentage = (report['files_missing'] / report['total_deleted_posts']) * 100
        report_text.append(f"  Files found: {found_percentage:.1f}%")
        report_text.append(f"  Files missing: {missing_percentage:.1f}%")

    report_text.append("")

    # Search directories
    report_text.append("SEARCH DIRECTORIES:")
    for directory in report['search_directories']:
        report_text.append(f"  - {directory}")
    report_text.append("")

    # Found files
    if report['found_posts']:
        report_text.append("FOUND FILES:")
        report_text.append("-" * 80)

        for post in report['found_posts']:
            report_text.append(f"Post ID: {post['post_id']}")
            report_text.append(f"  Expected: {post['file_name'] or 'N/A'}")
            report_text.append(f"  Found at: {post['found_path']}")
            report_text.append(f"  MD5: {post['md5'] or 'N/A'}")
            report_text.append("")

        report_text.append("")

    # Missing files
    if report['missing_posts']:
        report_text.append("MISSING FILES:")
        report_text.append("-" * 80)

        for post in report['missing_posts']:
            report_text.append(f"Post ID: {post['post_id']}")
            report_text.append(f"  File Name: {post['file_name'] or 'N/A'}")
            report_text.append(f"  MD5: {post['md5'] or 'N/A'}")
            report_text.append(f"  Rating: {post['rating'] or 'N/A'}")
            report_text.append(f"  Deleted: {post['deleted_at'] or 'N/A'}")
            if post.get('deletion_reason'):
                report_text.append(f"  Deletion Reason: {post['deletion_reason']}")
            report_text.append(f"  E621 URL: {post['e621_url']}")
            if post['file_url'] and post['file_url'] != post['e621_url']:
                report_text.append(f"  Original URL: {post['file_url']}")
            report_text.append("")
    else:
        report_text.append("All deleted posts have their files present!")
        report_text.append("")

    report_text.append("=" * 80)

    final_report = "\n".join(report_text)

    # Save to file if requested
    if output_file:
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(final_report)
            logging.info(f"Report saved to: {output_file}")
        except Exception as e:
            logging.error(f"Failed to save report to {output_file}: {e}")

    return final_report


def main(argv=None):
    """Main entry point. fff: argv is accepted for the CLI but unused."""
    # Setup logging
    log_file = setup_script_logging()
    logging.info("Starting enhanced deleted posts file verification")
    logging.info(f"Log file: {log_file}")

    try:
        # Load configuration
        config = load_config()

        # For testing, you can limit the number of posts
        # Change max_posts to None to check all posts
        max_posts = 10  # Set to None for full check
        verbose_logging = True  # Set to True for detailed debug info

        # Run the enhanced check
        report = check_deleted_posts_files_enhanced(config, max_posts=max_posts, verbose_logging=verbose_logging)

        # Generate report
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = str(archive_paths.ensure(archive_paths.REPORTS_DIR) / f"deleted_posts_enhanced_report_{timestamp}.txt")  # fff: was the CWD

        report_text = generate_enhanced_report(report, report_file)

        # Print summary to console
        print("\n" + report_text)

        logging.info("Enhanced deleted posts file verification completed")
        logging.info(f"Report saved to: {report_file}")

    except Exception as e:
        logging.error(f"Script failed: {e}", exc_info=True)
        print(f"Script failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
