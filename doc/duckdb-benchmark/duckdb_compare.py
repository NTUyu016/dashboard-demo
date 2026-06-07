import sqlite3
import argparse
import sqlite3
import time
import duckdb
import pandas as pd
import numpy as np


def chunk_generator(total_rows, chunk_size, seed=42):
    """Yield DataFrame chunks with deterministic pseudo-random data."""
    suppliers = [f"Supplier_{i}" for i in range(1, 51)]
    categories = ['Raw Material', 'Equipment', 'Chemical', 'Logistics']
    departments = ['Fab 12', 'Fab 15', 'Fab 18', 'HQ']
    statuses = ['Completed', 'Pending', 'Cancelled']

    rng = np.random.RandomState(seed)
    produced = 0
    next_id = 1
    while produced < total_rows:
        size = min(chunk_size, total_rows - produced)
        df = pd.DataFrame({
            'purchase_id': np.arange(next_id, next_id + size, dtype=np.int64),
            'year': 2020 + (np.arange(size) % 7),
            'supplier': rng.choice(suppliers, size),
            'item_category': rng.choice(categories, size),
            'quantity': rng.randint(1, 1000, size),
            'unit_price': rng.uniform(10.0, 500.0, size),
            'purchase_amount': rng.uniform(100.0, 50000.0, size),
            'department': rng.choice(departments, size),
            'status': rng.choice(statuses, size),
        })
        produced += size
        next_id += size
        yield df


def create_tables(sqlite_conn, duck_conn):
    schema = (
        "purchase_id INTEGER, year INTEGER, supplier TEXT, item_category TEXT,"
        " quantity INTEGER, unit_price REAL, purchase_amount REAL, department TEXT, status TEXT"
    )
    sqlite_conn.execute(f"CREATE TABLE purchases ({schema})")
    duck_conn.execute(f"CREATE TABLE purchases ({schema})")


def populate_databases(total_rows, chunk_size=1000000):
    sqlite_conn = sqlite3.connect(":memory:")
    duck_conn = duckdb.connect(":memory:")
    create_tables(sqlite_conn, duck_conn)

    for i, chunk in enumerate(chunk_generator(total_rows, chunk_size)):
        sqlite_conn.executemany(
            "INSERT INTO purchases VALUES (?,?,?,?,?,?,?,?,?)",
            chunk.to_records(index=False).tolist()
        )
        duck_conn.register(f"tmp_df_{i}", chunk)
        duck_conn.execute(f"INSERT INTO purchases SELECT * FROM tmp_df_{i}")

    return sqlite_conn, duck_conn


def run_query_and_time(sql_conn, duck_conn):
    test_sql = (
        "SELECT year, supplier, SUM(purchase_amount) as total_amount, "
        "AVG(purchase_amount) as avg_amount FROM purchases GROUP BY year, supplier "
        "ORDER BY year, total_amount DESC"
    )

    start = time.perf_counter()
    cur = sql_conn.cursor()
    cur.execute(test_sql)
    _ = cur.fetchall()
    sqlite_time = time.perf_counter() - start

    start = time.perf_counter()
    _ = duck_conn.execute(test_sql).fetchall()
    duck_time = time.perf_counter() - start

    speedup = sqlite_time / duck_time if duck_time > 0 else float('inf')
    return sqlite_time, duck_time, speedup


def main():
    parser = argparse.ArgumentParser(description='Compare SQLite vs DuckDB OLAP query time')
    parser.add_argument('--sizes', nargs='+', type=int, default=[1_000_000, 10_000_000],
                        help='List of row counts to test')
    parser.add_argument('--chunk-size', type=int, default=1_000_000,
                        help='Chunk size for incremental population')
    parser.add_argument('--run-large', action='store_true', help='Allow very large runs (e.g. 100M)')
    args = parser.parse_args()

    for n in args.sizes:
        if n >= 100_000_000 and not args.run_large:
            print(f"跳過 {n:,} 筆（需要加上 --run-large 才會執行大規模測試）")
            continue

        print(f"\n=== 測試 {n:,} 筆資料 ===")
        sqlite_conn, duck_conn = populate_databases(n, chunk_size=args.chunk_size)
        s_time, d_time, speedup = run_query_and_time(sqlite_conn, duck_conn)
        print(f"SQLite: {s_time:.4f}s | DuckDB: {d_time:.4f}s | DuckDB 約 {speedup:.1f} 倍")

        sqlite_conn.close()
        duck_conn.close()


if __name__ == '__main__':
    main()