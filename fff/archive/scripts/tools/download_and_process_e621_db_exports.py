#!/usr/bin/env python3
# fff: logs go to logs/archive/; exports go to db_export.directory (data/db_exports) instead of ./db_exports.
# fff: fixed: download_file passed timeout= to api_get, which doesn't accept it, so every download failed.
"""
E621 Database Export Downloader and Processor

Downloads the latest e621 database export files and loads them into MySQL database
with table names prefixed with 'e621_db_dump_'.

Files downloaded:
- posts-YYYY-MM-DD.csv.gz
- tag_aliases-YYYY-MM-DD.csv.gz
- tag_implications-YYYY-MM-DD.csv.gz
- tags-YYYY-MM-DD.csv.gz
"""

import os
import sys
import logging
import gzip
import csv
import requests
from datetime import datetime
from pathlib import Path

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql
from mysql.connector import Error

try:
    from fff.archive.download import api_get
except Exception:
    api_get = None


def setup_logging():
    """Set up logging configuration."""
    log_dir = archive_paths.LOG_DIR  # fff: was ./logs
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_db_export.log"
    log_path = log_dir / log_file_name

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler()
        ]
    )

    return log_path


def get_current_date():
    """Get current date in YYYY-MM-DD format."""
    return datetime.now().strftime('%Y-%m-%d')


def get_download_urls(date_str, config):
    """Generate download URLs for the given date based on config."""
    base_url = "https://e621.net/db_export"
    files = []

    db_export_config = config.get('db_export', {})

    if db_export_config.get('download_posts', True):
        files.append(f"posts-{date_str}.csv.gz")
    if db_export_config.get('download_tags', True):
        files.append(f"tags-{date_str}.csv.gz")
    if db_export_config.get('download_tag_aliases', True):
        files.append(f"tag_aliases-{date_str}.csv.gz")
    if db_export_config.get('download_tag_implications', True):
        files.append(f"tag_implications-{date_str}.csv.gz")

    return {file: f"{base_url}/{file}" for file in files}


def download_file(url, local_path, timeout=300):
    """Download a file from URL to local path with progress indication."""
    logging.info(f"Downloading {url} to {local_path}")

    try:
        if api_get:
            response = api_get(url, stream=True)  # fff: api_get has no timeout parameter (was a TypeError)
        else:
            response = requests.get(url, stream=True, timeout=timeout)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        downloaded_size = 0

        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded_size += len(chunk)

                    if total_size > 0:
                        progress = (downloaded_size / total_size) * 100
                        print(f"\rProgress: {progress:.1f}%", end="", flush=True)

        print()  # New line after progress
        logging.info(f"Successfully downloaded {local_path}")
        return True

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download {url}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error downloading {url}: {e}")
        return False


def extract_gz_file(gz_path, csv_path):
    """Extract a .gz file to CSV with progress tracking for large files."""
    logging.info(f"Extracting {gz_path} to {csv_path}")

    try:
        # Get compressed file size for progress estimation
        compressed_size = os.path.getsize(gz_path)
        logging.info(f"Compressed file size: {compressed_size / (1024*1024):.1f} MB")

        processed_size = 0
        extracted_size = 0
        last_progress_log = 0

        with gzip.open(gz_path, 'rb') as gz_file:
            with open(csv_path, 'wb') as csv_file:
                # Use larger chunk size for better performance on large files
                chunk_size = 1024 * 1024  # 1MB chunks for large files

                while True:
                    chunk = gz_file.read(chunk_size)
                    if not chunk:
                        break

                    csv_file.write(chunk)
                    processed_size += chunk_size
                    extracted_size += len(chunk)

                    # Show progress every 10MB of compressed data or 100MB extracted
                    if (processed_size - last_progress_log) >= (10 * 1024 * 1024):
                        progress_percent = (processed_size / compressed_size) * 100 if compressed_size > 0 else 0
                        logging.info(f"Extraction progress: {progress_percent:.1f}% - Extracted: {extracted_size / (1024*1024):.1f} MB")
                        last_progress_log = processed_size

        # Verify the file was extracted properly
        final_size = os.path.getsize(csv_path)
        if final_size > 0:
            logging.info(f"Successfully extracted {csv_path} ({final_size / (1024*1024):.1f} MB)")
            return True
        else:
            logging.error(f"Extracted file {csv_path} is empty")
            return False

    except Exception as e:
        logging.error(f"Failed to extract {gz_path}: {e}")
        return False
def create_db_tables(conn):
    """Create database tables for the export data."""
    cursor = conn.cursor()

    table_schemas = {
        'e621_db_dump_posts': """
            CREATE TABLE IF NOT EXISTS e621_db_dump_posts (
                id INT PRIMARY KEY,
                uploader_id INT,
                created_at DATETIME,
                md5 VARCHAR(32),
                source TEXT,
                rating VARCHAR(1),
                image_width INT,
                image_height INT,
                tag_string TEXT,
                locked_tags TEXT,
                fav_count INT,
                file_ext VARCHAR(10),
                parent_id INT,
                change_seq BIGINT,
                approver_id INT,
                file_size BIGINT,
                comment_count INT,
                description TEXT,
                duration FLOAT,
                updated_at DATETIME,
                is_deleted BOOLEAN,
                is_pending BOOLEAN,
                is_flagged BOOLEAN,
                score INT,
                up_score INT,
                down_score INT,
                is_rating_locked BOOLEAN,
                is_status_locked BOOLEAN,
                is_note_locked BOOLEAN,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """,

        'e621_db_dump_tags': """
            CREATE TABLE IF NOT EXISTS e621_db_dump_tags (
                id INT PRIMARY KEY,
                name VARCHAR(255),
                category INT,
                post_count INT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """,

        'e621_db_dump_tag_aliases': """
            CREATE TABLE IF NOT EXISTS e621_db_dump_tag_aliases (
                id INT PRIMARY KEY,
                antecedent_name VARCHAR(255),
                consequent_name VARCHAR(255),
                created_at DATETIME,
                status VARCHAR(50),
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """,

        'e621_db_dump_tag_implications': """
            CREATE TABLE IF NOT EXISTS e621_db_dump_tag_implications (
                id INT PRIMARY KEY,
                antecedent_name VARCHAR(255),
                consequent_name VARCHAR(255),
                created_at DATETIME,
                status VARCHAR(50),
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """
    }

    for table_name, schema in table_schemas.items():
        try:
            logging.info(f"Creating table {table_name}")
            cursor.execute(schema)
            conn.commit()
            logging.info(f"Table {table_name} created successfully")
        except Error as e:
            logging.error(f"Failed to create table {table_name}: {e}")
            raise

    cursor.close()


def convert_boolean_values(row, headers, table_name):
    """Convert 't'/'f' boolean values to 1/0 for MySQL compatibility."""
    if table_name != 'e621_db_dump_posts':
        return row

    # Boolean column positions for posts table
    boolean_columns = {
        'is_deleted': 20,
        'is_pending': 21,
        'is_flagged': 22,
        'is_rating_locked': 26,
        'is_status_locked': 27,
        'is_note_locked': 28
    }

    converted_row = row.copy()
    for col_name, col_index in boolean_columns.items():
        if col_index < len(converted_row):
            if converted_row[col_index] == 't':
                converted_row[col_index] = 1
            elif converted_row[col_index] == 'f':
                converted_row[col_index] = 0
            elif converted_row[col_index] == '':
                converted_row[col_index] = None

    return converted_row


def load_csv_to_mysql(conn, csv_path, table_name, config):
    """Load CSV data into MySQL table with progress tracking for large files."""
    file_size = os.path.getsize(csv_path)
    logging.info(f"Loading {csv_path} ({file_size / (1024*1024):.1f} MB) into table {table_name}")

    cursor = conn.cursor()
    db_export_config = config.get('db_export', {})

    try:
        # Clear existing data
        logging.info(f"Clearing existing data from {table_name}")
        cursor.execute(f"DELETE FROM {table_name}")
        conn.commit()

        # Read CSV and insert data
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            csv_reader = csv.reader(csvfile)
            headers = next(csv_reader)  # Skip header row

            # Prepare INSERT statement with ON DUPLICATE KEY UPDATE for posts table
            placeholders = ', '.join(['%s'] * len(headers))

            if table_name == 'e621_db_dump_posts':
                # Use REPLACE INTO for posts to handle updates
                insert_query = f"REPLACE INTO {table_name} ({','.join(headers)}) VALUES ({placeholders})"
                batch_size = db_export_config.get('batch_size_posts', 500)
            else:
                # Use INSERT IGNORE for other tables to avoid duplicates
                insert_query = f"INSERT IGNORE INTO {table_name} ({','.join(headers)}) VALUES ({placeholders})"
                batch_size = db_export_config.get('batch_size_other', 1000)

            progress_interval = db_export_config.get('progress_interval', 50000)

            batch_data = []
            row_count = 0
            last_progress_log = 0

            for row in csv_reader:
                # Convert empty strings to None for proper NULL handling
                processed_row = [None if cell == '' else cell for cell in row]

                # Convert boolean values for posts table
                processed_row = convert_boolean_values(processed_row, headers, table_name)

                batch_data.append(processed_row)
                row_count += 1

                if len(batch_data) >= batch_size:
                    cursor.executemany(insert_query, batch_data)
                    conn.commit()
                    batch_data = []

                    # Log progress every N rows for large files
                    if (row_count - last_progress_log) >= progress_interval:
                        logging.info(f"Processed {row_count:,} rows...")
                        last_progress_log = row_count            # Insert remaining data
            if batch_data:
                cursor.executemany(insert_query, batch_data)
                conn.commit()

            logging.info(f"Successfully loaded {row_count} rows into {table_name}")

    except Exception as e:
        logging.error(f"Failed to load {csv_path} into {table_name}: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()


def cleanup_files(file_paths):
    """Clean up downloaded and extracted files."""
    logging.info("Cleaning up temporary files...")

    for file_path in file_paths:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logging.info(f"Removed {file_path}")
        except Exception as e:
            logging.warning(f"Failed to remove {file_path}: {e}")


def check_existing_files(downloads_dir, current_date, config):
    """Check if files for current date exist and get their status."""
    db_export_config = config.get('db_export', {})
    expected_files = []

    if db_export_config.get('download_posts', True):
        expected_files.append(f"posts-{current_date}.csv.gz")
    if db_export_config.get('download_tags', True):
        expected_files.append(f"tags-{current_date}.csv.gz")
    if db_export_config.get('download_tag_aliases', True):
        expected_files.append(f"tag_aliases-{current_date}.csv.gz")
    if db_export_config.get('download_tag_implications', True):
        expected_files.append(f"tag_implications-{current_date}.csv.gz")

    existing_gz_files = []
    existing_csv_files = []

    for file_name in expected_files:
        gz_path = downloads_dir / file_name
        csv_path = downloads_dir / file_name.replace('.gz', '')

        if gz_path.exists():
            existing_gz_files.append(gz_path)
        if csv_path.exists():
            existing_csv_files.append(csv_path)

    return existing_gz_files, existing_csv_files, len(existing_gz_files) == len(expected_files)


def cleanup_old_files(downloads_dir, current_date):
    """Remove files that don't match the current date."""
    if not downloads_dir.exists():
        return

    logging.info("Cleaning up old files from previous dates...")
    removed_count = 0

    for file_path in downloads_dir.iterdir():
        if file_path.is_file():
            # Check if file contains a date that's not today
            file_name = file_path.name
            if ('-202' in file_name and current_date not in file_name):
                try:
                    file_path.unlink()
                    logging.info(f"Removed old file: {file_name}")
                    removed_count += 1
                except Exception as e:
                    logging.warning(f"Failed to remove old file {file_name}: {e}")

    if removed_count > 0:
        logging.info(f"Removed {removed_count} old files")
    else:
        logging.info("No old files to remove")


def main(argv=None):
    """Main execution function. fff: argv is accepted for the CLI but unused."""
    log_path = setup_logging()
    logging.info("Starting E621 Database Export Download and Processing")

    try:
        # Load configuration
        config = load_config()
        mysql_config = config["mysql"]
        db_export_config = config.get("db_export", {})

        # Log which files are enabled for download
        enabled_files = []
        if db_export_config.get('download_posts', True):
            enabled_files.append('posts')
        if db_export_config.get('download_tags', True):
            enabled_files.append('tags')
        if db_export_config.get('download_tag_aliases', True):
            enabled_files.append('tag_aliases')
        if db_export_config.get('download_tag_implications', True):
            enabled_files.append('tag_implications')

        logging.info(f"Enabled files for download: {', '.join(enabled_files)}")

        # Get current date
        current_date = get_current_date()
        logging.info(f"Using date: {current_date}")

        # Generate download URLs
        download_urls = get_download_urls(current_date, config)

        # Create downloads directory
        downloads_dir = Path(db_export_config.get("directory") or "db_exports")  # fff: config archive.db_export.directory (was ./db_exports)
        downloads_dir.mkdir(parents=True, exist_ok=True)

        # Check existing files
        existing_gz_files, existing_csv_files, all_files_exist = check_existing_files(downloads_dir, current_date, config)

        downloaded_files = []
        extracted_files = []

        if all_files_exist:
            logging.info(f"All files for {current_date} already exist. Skipping download.")
            downloaded_files = existing_gz_files
            extracted_files = existing_csv_files
        else:
            # Clean up old files from previous dates
            cleanup_old_files(downloads_dir, current_date)

            # Download files
            logging.info("WARNING: The posts file is very large and may take significant time to download and extract!")
            logging.info("Expected processing time: 30-60 minutes for posts file depending on your system")

            for file_name, url in download_urls.items():
                local_gz_path = downloads_dir / file_name
                local_csv_path = downloads_dir / file_name.replace('.gz', '')

                # Skip if both files already exist
                if local_gz_path.exists() and local_csv_path.exists():
                    logging.info(f"Files {file_name} and {file_name.replace('.gz', '')} already exist. Skipping.")
                    downloaded_files.append(local_gz_path)
                    extracted_files.append(local_csv_path)
                    continue

                # Download if needed
                if not local_gz_path.exists():
                    if download_file(url, local_gz_path):
                        downloaded_files.append(local_gz_path)
                    else:
                        logging.error(f"Failed to download {file_name}")
                        continue
                else:
                    downloaded_files.append(local_gz_path)

                # Extract if needed
                if not local_csv_path.exists():
                    if 'posts' in file_name:
                        logging.info("Starting extraction of large posts file - this may take 10-20 minutes...")

                    if extract_gz_file(local_gz_path, local_csv_path):
                        extracted_files.append(local_csv_path)
                    else:
                        logging.error(f"Failed to extract {file_name}")
                        continue
                else:
                    extracted_files.append(local_csv_path)

        if not extracted_files:
            logging.error("No files were successfully downloaded and extracted")
            return 1

        # Connect to database
        logging.info("Connecting to MySQL database...")
        conn = connect_to_mysql(mysql_config)
        if not conn:
            logging.error("Failed to connect to database")
            return 1

        try:
            # Create tables
            create_db_tables(conn)

            # Load data into tables
            table_mapping = {
                f'posts-{current_date}.csv': 'e621_db_dump_posts',
                f'tags-{current_date}.csv': 'e621_db_dump_tags',
                f'tag_aliases-{current_date}.csv': 'e621_db_dump_tag_aliases',
                f'tag_implications-{current_date}.csv': 'e621_db_dump_tag_implications'
            }

            for csv_file in extracted_files:
                file_name = csv_file.name
                if file_name in table_mapping:
                    table_name = table_mapping[file_name]
                    load_csv_to_mysql(conn, csv_file, table_name, config)
                else:
                    logging.warning(f"No table mapping found for {file_name}")

            logging.info("Database loading completed successfully!")

        finally:
            conn.close()

        # Use config setting for file cleanup
        keep_files = db_export_config.get('keep_files', True)

        if keep_files:
            logging.info("Keeping downloaded and extracted files for future use")
            logging.info(f"Files saved in: {downloads_dir}")
        else:
            cleanup_files(downloaded_files + extracted_files)

        logging.info("E621 Database Export processing completed successfully")
        return 0

    except KeyboardInterrupt:
        logging.info("Process interrupted by user")
        return 1
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
