# fff: implemented in media sorter/table_space_report.py.
# fff: imports from fff.sorter; main(argv=None) (no options).
"""Report total file_size (bytes) tracked per table, across every table that has a file_size column."""
from fff.sorter.handlers.database_manager import DatabaseManager
from fff.sorter.config import Config


def human_size(num_bytes):
    if num_bytes is None:
        return "0 B"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024 or unit == "PB":
            return f"{size:.2f} {unit}"
        size /= 1024


def main(argv=None):
    config = Config('configs/config.yaml')
    db = DatabaseManager(config)

    conn = db.get_db_connection()
    cursor = conn.cursor()

    # Find every table in this database that has a file_size column
    cursor.execute("""
        SELECT TABLE_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND COLUMN_NAME = 'file_size'
        ORDER BY TABLE_NAME
    """, (db.database,))
    tables = [row[0] for row in cursor.fetchall()]

    results = []
    for table in tables:
        cursor.execute(f"""
            SELECT COUNT(*), COALESCE(SUM(file_size), 0)
            FROM `{table}`
        """)
        row_count, total_bytes = cursor.fetchone()
        results.append((table, row_count, total_bytes))

    cursor.close()
    conn.close()

    results.sort(key=lambda r: r[2], reverse=True)

    name_width = max((len(r[0]) for r in results), default=5)
    print(f"{'Table'.ljust(name_width)}  {'Rows':>10}  {'Total Size':>12}")
    print("-" * (name_width + 27))
    grand_rows = 0
    grand_bytes = 0
    for table, row_count, total_bytes in results:
        print(f"{table.ljust(name_width)}  {row_count:>10}  {human_size(total_bytes):>12}")
        grand_rows += row_count
        grand_bytes += total_bytes

    print("-" * (name_width + 27))
    print(f"{'TOTAL'.ljust(name_width)}  {grand_rows:>10}  {human_size(grand_bytes):>12}")


if __name__ == "__main__":
    main()
