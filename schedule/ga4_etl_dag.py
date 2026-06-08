# -*- coding: utf-8 -*-
"""
Airflow DAG：每月定期執行 GA4 ETL（事件表 / 商品表 / 新用戶表）。

排程：每月 1 號 03:00（Asia/Taipei）跑一次，
      自動換算成「上一個完整月份」的日期區間，餵給 etl.py。
      etl.py 一次產出三張表（事件 / 商品 / 新用戶）。

例：2021-02-01 03:00 觸發 → 抓 2021-01-01 ~ 2021-01-31 的資料。

─────────────────────────────────────────────────────────────
憑證（OIDC / 服務帳戶）── 目前留白，待後續討論後填入
─────────────────────────────────────────────────────────────
兩支 ETL 透過 pandas_gbq 連 BigQuery，認證採 Google ADC
(Application Default Credentials)。正式排程不能用瀏覽器 OAuth，
請二選一（之後一起決定）：

  (A) Workload Identity Federation / OIDC（建議，無長期金鑰）
      由外部 IdP 取得短期 token，設定環境變數：
        GOOGLE_APPLICATION_CREDENTIALS=/path/to/oidc-credential-config.json
        GOOGLE_CLOUD_PROJECT=gen-lang-client-0283218135

  (B) 服務帳戶金鑰檔（較簡單但有長期金鑰風險）
        GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

設定方式擇一：
  1. 在 Airflow worker 的環境變數設定（docker-compose / k8s env）。
  2. 或填進下方 GCP_AUTH_ENV，由本 DAG 注入到 task 執行環境。
"""
from __future__ import annotations

import os
from datetime import datetime

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator

# ── 路徑設定：指向放置 etl.py 的專案目錄 ──
# 預設假設 schedule/ 與 etl.py 在同一個 dashboard-demo 專案下（schedule 的上一層）。
# 若 Airflow 部署在他處，改成絕對路徑即可。
PROJECT_DIR = os.environ.get(
    "GA4_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
PYTHON_BIN = os.environ.get("GA4_PYTHON_BIN", "python")

# ── 憑證注入（待填）──────────────────────────────────────────
# 等 OIDC / 服務帳戶決定後，把認證所需的環境變數填進這個 dict。
# 範例：
#   GCP_AUTH_ENV = {
#       "GOOGLE_APPLICATION_CREDENTIALS": "/opt/airflow/secrets/gcp-oidc.json",
#       "GOOGLE_CLOUD_PROJECT": "gen-lang-client-0283218135",
#   }
GCP_AUTH_ENV: dict[str, str] = {}

# task 執行環境 = 繼承 worker 環境 + 我們指定的憑證變數
TASK_ENV = {**os.environ, **GCP_AUTH_ENV}

# ── 日期區間（Jinja 模板，於執行時計算「上一個完整月份」）──
# 月排程下：data_interval_start = 上月 1 號，data_interval_end = 本月 1 號。
# END 取 data_interval_end 的前一天 = 上月最後一天。
START_DS = "{{ data_interval_start.strftime('%Y%m%d') }}"
END_DS = "{{ (data_interval_end - macros.timedelta(days=1)).strftime('%Y%m%d') }}"

default_args = {
    "owner": "data-eng",
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=10),
}

with DAG(
    dag_id="ga4_monthly_etl",
    description="每月抓取 GA4 上一個月的事件 / 商品 / 新用戶資料並落地為 Parquet",
    default_args=default_args,
    # 每月 1 號 03:00（時區見 start_date 的 tz）
    schedule="0 3 1 * *",
    start_date=pendulum.datetime(2021, 1, 1, tz="Asia/Taipei"),
    catchup=False,                # 不回補歷史月份；要回補改 True
    max_active_runs=1,            # 同一時間只跑一個 run，避免併發打 BigQuery
    tags=["ga4", "bigquery", "etl", "monthly"],
) as dag:

    # etl.py 一次產出三張表（事件 / 商品 / 新用戶）
    run_etl = BashOperator(
        task_id="run_etl",
        bash_command=(
            f'cd "{PROJECT_DIR}" && '
            f'"{PYTHON_BIN}" etl.py {START_DS} {END_DS}'
        ),
        env=TASK_ENV,
        append_env=False,
    )
