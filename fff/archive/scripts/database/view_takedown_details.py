#!/usr/bin/env python3
# fff: the interactive input() menu is kept as-is.
# fff: main(argv) parses an option-less argparse parser so --help works; the original took no arguments.
"""
Utility script to view takedown details from the database.

This script allows you to:
- View all takedowns
- View details for a specific takedown
- View all posts affected by a takedown
- View all takedowns affecting a specific post
"""

import argparse  # fff: for main(argv)
import sys
import logging
from pathlib import Path


from fff.archive.config import load_config
from fff.archive.db import (
    connect_to_mysql,
    get_takedown_by_id,
    get_posts_by_takedown,
    get_takedowns_by_post
)


def setup_logging():
    """Setup basic logging."""
    logging.basicConfig(
        level=logging.WARNING,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )


def list_all_takedowns(conn, limit=50):
    """List all takedowns in the database."""
    cursor = conn.cursor(dictionary=True)
    try:
        query = """
            SELECT
                takedown_id,
                LEFT(reason, 80) as reason_preview,
                status,
                finalized_at,
                scraped_at,
                (SELECT COUNT(*) FROM post_takedowns WHERE takedown_id = takedowns.takedown_id) as post_count
            FROM takedowns
            ORDER BY finalized_at DESC, scraped_at DESC
            LIMIT %s
        """
        cursor.execute(query, (limit,))
        results = cursor.fetchall()

        if not results:
            print("No takedowns found in database.")
            return

        print(f"\n{'ID':<10} {'Posts':<7} {'Status':<12} {'Finalized At':<20} {'Reason Preview':<80}")
        print("-" * 135)

        for row in results:
            takedown_id = row['takedown_id']
            post_count = row['post_count'] or 0
            status = row['status'] or 'unknown'
            finalized = row['finalized_at'].strftime('%Y-%m-%d %H:%M:%S') if row['finalized_at'] else 'N/A'
            reason = row['reason_preview'] or '(no reason)'

            print(f"{takedown_id:<10} {post_count:<7} {status:<12} {finalized:<20} {reason:<80}")

        print()

    finally:
        cursor.close()


def view_takedown_details(conn, takedown_id):
    """View detailed information about a specific takedown."""
    takedown = get_takedown_by_id(conn, takedown_id)

    if not takedown:
        print(f"\nTakedown #{takedown_id} not found in database.")
        return

    print("\n" + "=" * 80)
    print(f"TAKEDOWN #{takedown['takedown_id']}")
    print("=" * 80)

    print(f"\nStatus: {takedown['status'] or 'unknown'}")

    if takedown['created_at']:
        print(f"Created: {takedown['created_at']}")
    if takedown['updated_at']:
        print(f"Updated: {takedown['updated_at']}")
    if takedown['finalized_at']:
        print(f"Finalized: {takedown['finalized_at']}")

    print(f"Scraped: {takedown['scraped_at']}")
    print(f"Last Updated: {takedown['last_updated']}")

    print(f"\nReason:")
    print("-" * 80)
    print(takedown['reason'] or '(no reason available)')
    print()

    if takedown['instructions']:
        print(f"\nInstructions:")
        print("-" * 80)
        print(takedown['instructions'])
        print()

    if takedown['admin_notes']:
        print(f"\nAdmin Notes:")
        print("-" * 80)
        print(takedown['admin_notes'])
        print()

    # Get affected posts
    post_ids = get_posts_by_takedown(conn, takedown_id)
    print(f"\nAffected Posts: {len(post_ids)}")
    if post_ids:
        print(f"Post IDs: {', '.join(map(str, post_ids[:20]))}")
        if len(post_ids) > 20:
            print(f"... and {len(post_ids) - 20} more")

    print()


def view_post_takedowns(conn, post_id):
    """View all takedowns affecting a specific post."""
    takedowns = get_takedowns_by_post(conn, post_id)

    if not takedowns:
        print(f"\nNo takedown information found for post {post_id}.")
        return

    print("\n" + "=" * 80)
    print(f"TAKEDOWNS AFFECTING POST {post_id}")
    print("=" * 80)

    for i, takedown in enumerate(takedowns, 1):
        print(f"\n[{i}] Takedown #{takedown['takedown_id']}")
        print(f"    Status: {takedown['status'] or 'unknown'}")

        if takedown['finalized_at']:
            print(f"    Finalized: {takedown['finalized_at']}")

        print(f"    Reason: {takedown['reason'][:100] if takedown['reason'] else '(no reason)'}")

        if takedown['instructions']:
            print(f"    Instructions: {takedown['instructions'][:100]}...")

        if takedown['admin_notes']:
            print(f"    Admin Notes: {takedown['admin_notes'][:100]}...")

    print()


def get_takedown_statistics(conn):
    """Get statistics about takedowns in the database."""
    cursor = conn.cursor(dictionary=True)
    try:
        # Total takedowns
        cursor.execute("SELECT COUNT(*) as count FROM takedowns")
        total_takedowns = cursor.fetchone()['count']

        # Total post-takedown links
        cursor.execute("SELECT COUNT(*) as count FROM post_takedowns")
        total_links = cursor.fetchone()['count']

        # Takedowns by status
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM takedowns
            GROUP BY status
            ORDER BY count DESC
        """)
        by_status = cursor.fetchall()

        # Average posts per takedown
        cursor.execute("""
            SELECT AVG(post_count) as avg_posts
            FROM (
                SELECT takedown_id, COUNT(*) as post_count
                FROM post_takedowns
                GROUP BY takedown_id
            ) as counts
        """)
        avg_result = cursor.fetchone()
        avg_posts = avg_result['avg_posts'] if avg_result['avg_posts'] else 0

        print("\n" + "=" * 80)
        print("TAKEDOWN STATISTICS")
        print("=" * 80)
        print(f"\nTotal Takedowns: {total_takedowns}")
        print(f"Total Post-Takedown Links: {total_links}")
        print(f"Average Posts per Takedown: {avg_posts:.2f}")

        if by_status:
            print("\nBy Status:")
            for row in by_status:
                status = row['status'] or 'unknown'
                count = row['count']
                print(f"  {status}: {count}")

        print()

    finally:
        cursor.close()


def main(argv=None):
    """Main entry point."""
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)  # fff: --help only
    setup_logging()

    try:
        config = load_config()
        mysql_config = config.get('mysql', {})

        conn = connect_to_mysql(mysql_config)
        if conn is None:
            print("Failed to connect to database")
            sys.exit(1)

        try:
            while True:
                print("\n" + "=" * 80)
                print("TAKEDOWN DETAILS VIEWER")
                print("=" * 80)
                print("\n1. List all takedowns")
                print("2. View specific takedown details")
                print("3. View takedowns for a specific post")
                print("4. View statistics")
                print("5. Exit")
                print()

                choice = input("Enter choice (1-5): ").strip()

                if choice == '1':
                    limit_str = input("How many takedowns to show? (default: 50): ").strip()
                    limit = int(limit_str) if limit_str.isdigit() else 50
                    list_all_takedowns(conn, limit)

                elif choice == '2':
                    takedown_id = input("Enter takedown ID: ").strip()
                    if takedown_id.isdigit():
                        view_takedown_details(conn, int(takedown_id))
                    else:
                        print("Invalid takedown ID")

                elif choice == '3':
                    post_id = input("Enter post ID: ").strip()
                    if post_id.isdigit():
                        view_post_takedowns(conn, int(post_id))
                    else:
                        print("Invalid post ID")

                elif choice == '4':
                    get_takedown_statistics(conn)

                elif choice == '5':
                    print("\nExiting...")
                    break

                else:
                    print("\nInvalid choice. Please enter 1-5.")

        finally:
            conn.close()

    except KeyboardInterrupt:
        print("\n\nExiting...")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Script failed: {e}", exc_info=True)
        print(f"Script failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
