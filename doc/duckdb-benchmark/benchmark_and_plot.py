import csv
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from duckdb_compare import chunk_generator, populate_databases, run_query_and_time


def print_first_rows(n=5):
    gen = chunk_generator(n, n)
    df = next(gen)
    # Print CSV-like header + rows
    print("\nFirst 5 rows (CSV-like):\n")
    print(",".join(df.columns.tolist()))
    for row in df.itertuples(index=False, name=None):
        print(",".join(str(x) for x in row))


def run_tests(sizes, chunk_size=1_000_000):
    results = []
    for n in sizes:
        print(f"\nRunning test for {n:,} rows...")
        sqlite_conn, duck_conn = populate_databases(n, chunk_size=chunk_size)
        s_time, d_time, speedup = run_query_and_time(sqlite_conn, duck_conn)
        print(f"Result: SQLite={s_time:.4f}s, DuckDB={d_time:.4f}s, speedup={speedup:.1f}x")
        results.append((n, s_time, d_time, speedup))
        sqlite_conn.close()
        duck_conn.close()
    return results


def save_csv(results, path="timings.csv"):
    with open(path, "w", newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(["rows", "sqlite_time_s", "duckdb_time_s", "speedup"])
        for r in results:
            w.writerow(r)
    print(f"Saved timings to {path}")


def plot_results(results, out="timings.png"):
    sizes = np.array([r[0] for r in results], dtype=float)
    sqlite_times = np.array([r[1] for r in results])
    duck_times = np.array([r[2] for r in results])
    # Plot using equal spacing on the x-axis and show multiplicative labels
    # e.g., if sizes = [1e6, 5e6, 25e6], labels become ['1x','5x','25x']
    base = sizes[0]
    multipliers = sizes / base

    x = np.arange(len(sizes))

    plt.figure(figsize=(8, 5))
    plt.plot(x, sqlite_times, marker='o', label='SQLite')
    plt.plot(x, duck_times, marker='o', label='DuckDB')
    plt.xlabel('Data size (multiples of first sample)')
    plt.ylabel('Query time (s)')
    plt.title('SQLite vs DuckDB query time')
    plt.grid(True)
    plt.legend()
    # set xticks to equal spacing and label as multiplicative factors
    labels = [f"{int(m)}x" if abs(m - round(m)) < 1e-8 else f"{m:.1f}x" for m in multipliers]
    plt.xticks(x, labels)
    plt.tight_layout()
    plt.savefig(out)
    print(f"Saved plot to {out}")


if __name__ == '__main__':
    # Sampling points: 1,000,000 ; 5,000,000 ; 25,000,000
    target_sizes = [1_000_000, 5_000_000, 25_000_000]

    # We'll run all three sizes (note: 25M may take significant time/memory).
    actual_sizes = target_sizes

    print_first_rows(5)

    results = run_tests(actual_sizes, chunk_size=1_000_000)

    # Extrapolate 100M using linear fit on sizes vs times for each DB
    sizes = np.array([r[0] for r in results], dtype=float)
    sqlite_times = np.array([r[1] for r in results])
    duck_times = np.array([r[2] for r in results])

    # Fit a line through origin or linear regression? We'll fit linear (degree=1)
    coef_sql = np.polyfit(sizes, sqlite_times, 1)
    coef_duck = np.polyfit(sizes, duck_times, 1)

    projected_size = 100_000_000
    proj_sql_time = np.polyval(coef_sql, projected_size)
    proj_duck_time = np.polyval(coef_duck, projected_size)

    results.append((projected_size, float(proj_sql_time), float(proj_duck_time), float(proj_sql_time / proj_duck_time)))

    save_csv(results, path="timings.csv")
    plot_results(results, out="timings.png")

    print("\nNote: 100,000,000 row result is extrapolated from measured points.")
