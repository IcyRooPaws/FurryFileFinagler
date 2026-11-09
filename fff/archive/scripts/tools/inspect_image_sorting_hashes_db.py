#!/usr/bin/env python3
# fff: connects to the active environment's sorter schema; main() takes no options (argv is ignored).
# fff: logging.basicConfig still runs at import time, as in the original.
"""
Script to inspect the image_sorting_hashes database structure
and understand the actual table schema for proper integration.
"""

import sys
import logging

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.common import db
from mysql.connector import Error

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def connect_to_image_sorting_hashes_db(mysql_config):
    """Connect to the image_sorting_hashes database."""
    try:
        # fff: prod or test_ sorter schema
        return db.connect_with(mysql_config, database=archive_paths.db_names()["sorter"])
    except Error as e:
        logging.error(f"Error connecting to image_sorting_hashes database: {e}")
        return None

def inspect_database_structure(conn):
    """Inspect and display the database structure."""
    cursor = conn.cursor()
    
    try:
        # Get all tables in the database
        print("=" * 60)
        print("IMAGE_SORTING_HASHES DATABASE STRUCTURE")
        print("=" * 60)
        
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        if not tables:
            print("No tables found in image_sorting_hashes database")
            return
        
        print(f"Found {len(tables)} table(s):")
        for table in tables:
            print(f"  - {table[0]}")
        print()
        
        # Inspect each table
        for table_tuple in tables:
            table_name = table_tuple[0]
            print(f"TABLE: {table_name}")
            print("-" * 40)
            
            # Get table structure
            cursor.execute(f"DESCRIBE {table_name}")
            columns = cursor.fetchall()
            
            print("COLUMNS:")
            for col in columns:
                field, col_type, null, key, default, extra = col
                print(f"  {field:<20} {col_type:<20} {key:<10} {null}")
            
            # Get sample data (first 5 rows)
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 5")
            sample_data = cursor.fetchall()
            
            # Get column names for sample data
            cursor.execute(f"SHOW COLUMNS FROM {table_name}")
            column_info = cursor.fetchall()
            column_names = [col[0] for col in column_info]
            
            print("\nSAMPLE DATA (first 5 rows):")
            if sample_data:
                # Print header
                print("  " + " | ".join(f"{name:<20}" for name in column_names))
                print("  " + "-" * (23 * len(column_names)))
                
                # Print data rows
                for row in sample_data:
                    formatted_row = []
                    for i, value in enumerate(row):
                        if value is None:
                            formatted_row.append("NULL".ljust(20))
                        else:
                            # Truncate long values
                            str_val = str(value)
                            if len(str_val) > 20:
                                str_val = str_val[:17] + "..."
                            formatted_row.append(str_val.ljust(20))
                    print("  " + " | ".join(formatted_row))
            else:
                print("  No data found")
            
            # Get row count
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = cursor.fetchone()[0]
            print(f"\nTOTAL ROWS: {row_count}")
            print("=" * 60)
            print()
            
    except Exception as e:
        logging.error(f"Error inspecting database structure: {e}")
    finally:
        cursor.close()

def analyze_hash_columns(conn):
    """Analyze potential hash columns in the database."""
    cursor = conn.cursor()
    
    try:
        print("HASH COLUMN ANALYSIS")
        print("=" * 40)
        
        # Get all tables
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        hash_columns_found = []
        
        for table_tuple in tables:
            table_name = table_tuple[0]
            
            # Get columns that might contain hashes
            cursor.execute(f"SHOW COLUMNS FROM {table_name}")
            columns = cursor.fetchall()
            
            potential_hash_columns = []
            filename_columns = []
            
            for col in columns:
                field_name = col[0].lower()
                field_type = col[1].lower()
                
                # Look for hash-like columns
                if 'hash' in field_name:
                    potential_hash_columns.append(col[0])
                
                # Look for filename-like columns
                if any(term in field_name for term in ['file', 'name', 'path']):
                    filename_columns.append(col[0])
            
            if potential_hash_columns:
                print(f"Table '{table_name}':")
                print(f"  Hash columns: {potential_hash_columns}")
                print(f"  Filename columns: {filename_columns}")
                
                # Sample some hash values to understand format
                for hash_col in potential_hash_columns:
                    cursor.execute(f"SELECT {hash_col} FROM {table_name} WHERE {hash_col} IS NOT NULL LIMIT 3")
                    sample_hashes = cursor.fetchall()
                    
                    if sample_hashes:
                        print(f"  Sample {hash_col} values:")
                        for i, hash_row in enumerate(sample_hashes):
                            hash_value = hash_row[0]
                            print(f"    {i+1}. {hash_value} (length: {len(str(hash_value))})")
                
                hash_columns_found.extend([(table_name, col) for col in potential_hash_columns])
                print()
        
        if not hash_columns_found:
            print("No hash columns found in any tables")
        else:
            print(f"Summary: Found {len(hash_columns_found)} potential hash columns")
            
    except Exception as e:
        logging.error(f"Error analyzing hash columns: {e}")
    finally:
        cursor.close()

def suggest_integration_code(conn):
    """Suggest code modifications based on the actual database structure."""
    cursor = conn.cursor()
    
    try:
        print("INTEGRATION CODE SUGGESTIONS")
        print("=" * 50)
        
        # Get all tables
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        suggested_queries = []
        
        for table_tuple in tables:
            table_name = table_tuple[0]
            
            # Get columns
            cursor.execute(f"SHOW COLUMNS FROM {table_name}")
            columns = cursor.fetchall()
            
            hash_columns = []
            filename_columns = []
            
            for col in columns:
                field_name = col[0].lower()
                
                if 'hash' in field_name:
                    hash_columns.append(col[0])
                
                if any(term in field_name for term in ['file', 'name', 'path']):
                    filename_columns.append(col[0])
            
            # Generate query suggestions
            if hash_columns and filename_columns:
                for hash_col in hash_columns:
                    for filename_col in filename_columns:
                        query = f"SELECT {hash_col} FROM {table_name} WHERE {filename_col} = %s"
                        suggested_queries.append(query)
        
        if suggested_queries:
            print("Suggested queries for check_media_hash_in_image_sorting_db function:")
            print("```python")
            print("possible_queries = [")
            for query in suggested_queries:
                print(f'    "{query}",')
            print("]")
            print("```")
        else:
            print("Could not generate query suggestions - no suitable columns found")
            
    except Exception as e:
        logging.error(f"Error generating suggestions: {e}")
    finally:
        cursor.close()

def main(argv=None):  # fff: argv accepted for the fff CLI; the original takes no arguments
    """Main function to inspect the image_sorting_hashes database."""
    
    print("Image Sorting Hashes Database Inspector")
    print("=" * 50)
    
    # Load configuration
    try:
        config = load_config()
        mysql_config = config.get("mysql", {})
    except Exception as e:
        logging.error(f"Error loading configuration: {e}")
        return 1
    
    # Connect to database
    conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not conn:
        print("[ERROR] Could not connect to image_sorting_hashes database")
        print("This is expected if the database doesn't exist")
        return 1
    
    print("[OK] Successfully connected to image_sorting_hashes database")
    print()
    
    try:
        # Inspect the database structure
        inspect_database_structure(conn)
        
        # Analyze hash columns
        analyze_hash_columns(conn)
        
        # Suggest integration code
        suggest_integration_code(conn)
        
    except Exception as e:
        logging.error(f"Error during inspection: {e}")
        return 1
    finally:
        conn.close()
    
    print("Database inspection complete!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
