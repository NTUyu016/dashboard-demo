# -*- coding: utf-8 -*-
"""
ETL：從 BigQuery 公開資料集 (GA4 obfuscated sample ecommerce)
下載事件表與產品表，落地為本地 Parquet 檔 (Snappy 壓縮)。

執行：
    python etl.py            # 預設抓 2021-01-01 單日
    python etl.py 20210101 20210131   # 自訂日期區間 (含頭含尾)

第一次執行會跳出瀏覽器要求 Google OAuth 授權。
"""
import math
import sys
import time

import pandas as pd
import pandas_gbq
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

PROJECT_ID = "gen-lang-client-0283218135"
OUT_DIR = "."

# ---- 日期區間 (對應 SQL 的 _TABLE_SUFFIX) ----
START = sys.argv[1] if len(sys.argv) > 1 else "20210101"
END = sys.argv[2] if len(sys.argv) > 2 else START

EVENTS_SQL = f"""
SELECT
    PARSE_DATE('%Y%m%d', event_date)            AS event_date,
    TIMESTAMP_MICROS(event_timestamp)           AS event_datetime,
    event_name,
    event_value_in_usd,
    user_pseudo_id,
    geo.country                                  AS country,
    geo.region                                   AS region,
    geo.city                                     AS city,
    geo.sub_continent                            AS sub_continent,
    geo.continent                                AS continent,
    device.category                              AS device_type,
    device.operating_system                      AS os,
    device.web_info.browser                      AS browser,
    device.language                              AS device_language,
    device.mobile_brand_name                     AS mobile_brand,
    traffic_source.name                          AS campaign_name,
    traffic_source.medium                        AS traffic_medium,
    traffic_source.source                        AS traffic_source,
    ecommerce.total_item_quantity                AS total_items_count,
    ecommerce.purchase_revenue_in_usd            AS total_purchase_revenue,
    ecommerce.transaction_id                     AS transaction_id,
    ecommerce.unique_items                        AS unique_items_count
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{START}' AND '{END}'
"""

PRODUCTS_SQL = f"""
SELECT
    PARSE_DATE('%Y%m%d', event_date)            AS event_date,
    user_pseudo_id,
    TIMESTAMP_MICROS(event_timestamp)           AS event_datetime,
    event_name,
    i.item_id                                    AS item_id,
    i.item_name                                  AS item_name,
    i.item_brand                                 AS item_brand,
    i.item_variant                               AS item_variant,
    i.item_category                              AS item_category,
    i.item_category2                             AS item_category2,
    i.price                                       AS item_price,
    i.quantity                                    AS item_quantity,
    (i.price * i.quantity)                       AS item_total_revenue,
    i.item_revenue                                AS item_reported_revenue,
    i.coupon                                      AS item_coupon_code,
    i.affiliation                                 AS store_affiliation,
    i.location_id                                 AS location_id
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
CROSS JOIN UNNEST(items) AS i
WHERE _TABLE_SUFFIX BETWEEN '{START}' AND '{END}'
"""


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

    path = f"{OUT_DIR}/{out}"
    print(f"[{name}] 2/2 寫出 Parquet（Snappy 壓縮）...")
    t1 = time.perf_counter()
    write_parquet_with_progress(df, path)
    print(f"[{name}]     已寫出 -> {path}（寫檔耗時 {time.perf_counter() - t1:.1f}s）")
    return df


if __name__ == "__main__":
    print(f"日期區間：{START} ~ {END}  |  project_id={PROJECT_ID}")
    extract("events", EVENTS_SQL, "events_data.parquet")
    extract("products", PRODUCTS_SQL, "products_data.parquet")
    print("\n[OK] ETL 完成")
