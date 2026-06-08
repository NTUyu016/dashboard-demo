# -*- coding: utf-8 -*-
"""
共用的「資料倉儲物化」邏輯：把 ETL 產出的 Parquet 物化成一個持久化的
DuckDB 檔 (ga4.duckdb)，內含儀表板實際用到的精簡欄位表。

設計目的（為什麼不在 app 啟動時 CREATE TABLE）：
  - 把「物化」歸位到 ETL 階段，只做一次；app 改為「唯讀開檔」，啟動接近即時。
  - 唯讀檔可被多個 gunicorn worker 進程同時開啟、共享 OS page cache，
    不再每個進程各自重建 1.4GB（避免多 worker 記憶體翻倍）。
  - 移除 app import 時的重 I/O 副作用。

只挑儀表板查詢實際用到的欄位 (而非 SELECT *)：檔案更小、查詢更快。
"""
import os

import duckdb

DB_FILENAME = "ga4.duckdb"

# 各表只物化儀表板查詢會用到的欄位
EVENT_COLS = (
    "event_date, event_datetime, event_name, country, device_type, "
    "traffic_medium, traffic_source, campaign_name, transaction_id, "
    "total_purchase_revenue, user_pseudo_id"
)
PRODUCT_COLS = (
    "event_date, event_datetime, event_name, item_id, item_name, item_brand, "
    "item_category, item_price, item_quantity, item_total_revenue, user_pseudo_id"
)
NEWUSER_COLS = (
    "first_visit_date, country, continent, device_type, os, "
    "traffic_medium, traffic_source, campaign_name, "
    "did_purchase, purchase_revenue_total, purchase_count, days_to_first_purchase"
)


def _p(path: str) -> str:
    """統一成 DuckDB 可吃的正斜線路徑。"""
    return path.replace("\\", "/")


def build_warehouse(db_path, events_parquet, products_parquet,
                    newuser_parquet=None):
    """從 Parquet 建立/覆寫持久化 DuckDB 檔；newusers 表在來源存在時才建。

    回傳實際建立的表名清單。建立後即關閉寫入連線，供 app 以唯讀方式開啟。
    """
    built = []
    con = duckdb.connect(db_path)  # 寫入連線
    try:
        con.execute(
            f"CREATE OR REPLACE TABLE events AS "
            f"SELECT {EVENT_COLS} FROM '{_p(events_parquet)}'"
        )
        built.append("events")
        con.execute(
            f"CREATE OR REPLACE TABLE products AS "
            f"SELECT {PRODUCT_COLS} FROM '{_p(products_parquet)}'"
        )
        built.append("products")
        if newuser_parquet and os.path.exists(newuser_parquet):
            con.execute(
                f"CREATE OR REPLACE TABLE newusers AS "
                f"SELECT {NEWUSER_COLS} FROM '{_p(newuser_parquet)}'"
            )
            built.append("newusers")
    finally:
        con.close()
    return built
