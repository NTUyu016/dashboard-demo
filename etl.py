# -*- coding: utf-8 -*-
"""
ETL：從 BigQuery 公開資料集 (GA4 obfuscated sample ecommerce)
下載並落地為本地 Parquet 檔 (Snappy 壓縮)。產出三張表：
    - events_data.parquet           逐事件明細
    - products_data.parquet         逐商品明細 (items 展開)
    - first_visit_users_data.parquet 新用戶 (first_visit) + 後續轉換旅程標記

SQL 全部抽到 sql/ 資料夾下的 .sql 公版檔，以 ${START} / ${END} 為日期參數，
不再 hard-code 在本檔，方便維護與版本控管。

執行：
    python etl.py            # 預設抓 2021-01-01 單日
    python etl.py 20210101 20210131   # 自訂日期區間 (含頭含尾)

第一次執行會跳出瀏覽器要求 Google OAuth 授權。
"""
import math
import os
import sys
import time

import pandas as pd
import pandas_gbq
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

import warehouse

PROJECT_ID = "gen-lang-client-0283218135"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQL_DIR = os.path.join(BASE_DIR, "sql")
OUT_DIR = BASE_DIR

# ---- 日期區間 (對應 SQL 的 _TABLE_SUFFIX) ----
START = sys.argv[1] if len(sys.argv) > 1 else "20210101"
END = sys.argv[2] if len(sys.argv) > 2 else START


def load_sql(name: str, **params) -> str:
    """讀取 sql/<name>.sql 公版，將 ${KEY} 佔位符換成實際參數值。"""
    path = os.path.join(SQL_DIR, f"{name}.sql")
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()
    for key, val in params.items():
        sql = sql.replace(f"${{{key}}}", str(val))
    return sql


def write_parquet_with_progress(df: pd.DataFrame, path: str, chunk_size: int = 200_000):
    """分塊寫出 Parquet，附 tqdm 進度條，讓落地過程可觀察。"""
    table = pa.Table.from_pandas(df, preserve_index=False)
    n_chunks = max(1, math.ceil(table.num_rows / chunk_size))
    writer = pq.ParquetWriter(path, table.schema, compression="snappy")
    try:
        for i in tqdm(range(n_chunks), desc="  寫出 Parquet", unit="chunk"):
            start = i * chunk_size
            writer.write_table(table.slice(start, chunk_size))
    finally:
        writer.close()


def extract(name: str, sql: str, out: str):
    print(f"\n[{name}] 1/2 查詢 BigQuery 中（下載進度如下）...")
    t0 = time.perf_counter()
    df = pandas_gbq.read_gbq(sql, project_id=PROJECT_ID, progress_bar_type="tqdm")
    print(f"[{name}]     取回 {len(df):,} 筆，下載耗時 {time.perf_counter() - t0:.1f}s")

    path = os.path.join(OUT_DIR, out)
    print(f"[{name}] 2/2 寫出 Parquet（Snappy 壓縮）...")
    t1 = time.perf_counter()
    write_parquet_with_progress(df, path)
    print(f"[{name}]     已寫出 -> {path}（寫檔耗時 {time.perf_counter() - t1:.1f}s）")
    return df


if __name__ == "__main__":
    print(f"日期區間：{START} ~ {END}  |  project_id={PROJECT_ID}")

    extract("events",   load_sql("events",   START=START, END=END), "events_data.parquet")
    extract("products", load_sql("products", START=START, END=END), "products_data.parquet")

    fv = extract("first_visit", load_sql("first_visit", START=START, END=END),
                 "first_visit_users_data.parquet")
    if len(fv):
        conv = fv["did_purchase"].mean() * 100
        print(f"\n[摘要] 新用戶 {len(fv):,} 人，其中曾購買 {int(fv['did_purchase'].sum()):,} 人"
              f"（新用戶轉換率 {conv:.2f}%）")

    # 物化成持久化 DuckDB 檔，讓儀表板啟動時只需唯讀開檔 (不再重建)
    print("\n[warehouse] 物化 Parquet -> DuckDB 檔 (供儀表板唯讀開啟)...")
    db_path = os.path.join(OUT_DIR, warehouse.DB_FILENAME)
    built = warehouse.build_warehouse(
        db_path,
        os.path.join(OUT_DIR, "events_data.parquet"),
        os.path.join(OUT_DIR, "products_data.parquet"),
        os.path.join(OUT_DIR, "first_visit_users_data.parquet"),
    )
    print(f"[warehouse]     已建立 {built} -> {db_path}")

    print("\n[OK] ETL 完成")
