# Web Traffic Dashboard Demo

> GA4 公開電商樣本資料的網站流量分析儀表板 · DuckDB + Plotly Dash

一個以 Google GA4 公開電商樣本（約 430 萬筆事件）打造的互動式分析儀表板：用 DuckDB 直接查詢 Parquet，前端以 Plotly Dash 呈現 KPI、趨勢、轉換漏斗、商品分析與**新用戶轉換**，並附後端分頁/排序明細。ETL 的 SQL 抽成獨立公版檔，另附一支可月排程的 Airflow DAG。

## 架構總覽

```
BigQuery 公開資料集 ──(etl.py + sql/*.sql)──▶ events_data.parquet
ga4_obfuscated_sample_ecommerce              products_data.parquet
                                             first_visit_users_data.parquet
                                                      │
                                                      ▼
                                   DuckDB：啟動時把所需欄位物化成記憶體表
                                   (CREATE TABLE，等同 DuckDB 版 MView)
                                                      │
                                                      ▼
                                   Plotly Dash (FLATLY) 儀表板
                                   http://127.0.0.1:8050

排程（選用）：schedule/ga4_etl_dag.py — Airflow 每月 1 號自動重跑 ETL
```

- **無需資料庫伺服器**：DuckDB 直接掃描本地 Parquet，免架 DB、免匯入。
- **記憶體物化、查詢即記憶體**：App 啟動時把儀表板實際用到的欄位以 `CREATE TABLE` 物化進 DuckDB 記憶體，之後每次查詢直接打記憶體欄式資料（相較每次重讀 Parquet 的 View，實測快約 3 倍）。

---

## 前置需求

- Python 3.9+
- Google Cloud 帳號（查詢 BigQuery 公開資料集，幾乎免費）

```bash
pip install -r requirements.txt
```

ETL 連 BigQuery 採 Google ADC（Application Default Credentials）。正式排程前，本機先做一次：

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project <你的 GCP project_id>
```

> 若未安裝 gcloud，`pandas_gbq` 也會在首次執行 `etl.py` 時自動跳出瀏覽器 OAuth 授權。

---

## 快速開始

### 步驟一：ETL — 從 BigQuery 下載資料（一次性）

`etl.py` 會依序產出三張表，SQL 取自 `sql/` 資料夾的公版檔（以 `${START}`/`${END}` 為日期參數）：

```bash
# 單日快速測試（預設 2021-01-01）
python etl.py

# 完整區間（約 430 萬筆事件 + 商品明細 + 新用戶表）
python etl.py 20201101 20210131
```

**執行時間參考：**

| 模式 | 日期範圍 | 預估筆數 | 預估時間 |
|------|----------|----------|----------|
| 快速測試 | 單日（預設） | ~10–20 萬筆 | **1–3 分鐘** |
| 完整區間 | 3 個月 | ~430 萬筆事件 + 商品 + 新用戶 | **15–30 分鐘** |

> 時間受網速與 BigQuery slot 負載影響；首次含 OAuth 授權會多 1–2 分鐘。

**產出檔案（皆 Snappy 壓縮，已列入 `.gitignore`）：**
- `events_data.parquet`：事件主表
- `products_data.parquet`：商品明細表（items 展開）
- `first_visit_users_data.parquet`：新用戶表（每位首訪用戶 + 是否購買/首購金額/首購距首訪天數等轉換標記）

---

### 步驟二：啟動儀表板

```bash
python app.py
```

開啟瀏覽器前往 **http://127.0.0.1:8050**

> 啟動約需數秒～十幾秒：DuckDB 會把所需欄位物化進記憶體（完整 3 個月資料約佔 ~1.4 GB），換取之後每次互動的毫秒級響應。

---

## 為什麼用 Parquet 而非 CSV？

ETL 落地刻意選 **Parquet（欄式儲存）** 而非 CSV，實測差異顯著（以本資料集 10 萬筆事件樣本量測）：

| 指標 | CSV | Parquet (Snappy) | 差異 |
|------|----:|----:|----:|
| 檔案大小（10 萬筆樣本） | 18.4 MB | 2.3 MB | **小 8.1 倍** |
| DuckDB 聚合查詢耗時 | 147.9 ms | 14.7 ms | **快 10.1 倍** |
| 全量換算（events 表） | ~775 MB | 95.9 MB | **省 88% 空間** |
| 全量換算（products 表） | ~439 MB | 54.3 MB | **省 88% 空間** |

**原因：**
- **欄式儲存**：查詢只讀取用到的欄位，不必掃整列 → 聚合/篩選 I/O 大幅降低。
- **內建壓縮 + 型別**：Snappy 壓縮 + 保留 schema，無需像 CSV 每次重新推斷型別。
- **DuckDB 原生支援**：可直接 `SELECT FROM 'file.parquet'`，零轉換、零匯入。

> 延伸驗證：[`doc/duckdb-benchmark/`](doc/duckdb-benchmark/) 進一步比較 **DuckDB（欄式）vs SQLite（列式）** 在千萬～億級資料上的 OLAP 聚合效能，DuckDB 快 **50–110 倍**（詳見該資料夾 README 與 [`timings.png`](doc/duckdb-benchmark/timings.png)）。

---

## 效能設計：為什麼用記憶體表而非 View

| 做法 | 速度 | 記憶體 | 說明 |
|------|------|--------|------|
| VIEW over Parquet | ~95 ms/查詢 | ~0 | 每次查詢回磁碟重讀、重解壓 |
| **記憶體表（本專案）** | **~32 ms/查詢** | ~1.4 GB | 啟動時 `CREATE TABLE` 只物化所需欄位 |

- DuckDB **沒有原生 Materialized View**，`CREATE TABLE AS SELECT` 即是其物化視圖的實作方式。
- 只物化儀表板實際用到的欄位（非 `SELECT *`），記憶體較全量低、速度不受影響。
- 不採「預聚合彙總表」是因為本 app 有 free-text 關鍵字搜尋與 `COUNT(DISTINCT)`（非可加總）KPI，預聚合無法忠實重現結果。
- 資料每月由 ETL 更新一次，與記憶體表「啟動即重建」的時機天然吻合，不會有 View 那種需手動 REFRESH 的過期問題。

---

## 功能說明

### 左側控制面板
| 控制項 | 說明 |
|--------|------|
| **日期區間** | 所有圖表/表格依時間窗動態切片；**預設最近 30 天**，讓「與前期比較」一開啟就有對照 |
| **事件名稱** | 多選，對應 GA4 `event_name` |
| **國家** | 多選，對應 `geo.country` |
| **商品類別** | 多選，對應 purchase 事件的 `item_category`（僅作用於商品分析頁）|
| **流量媒介** | 多選，對應 `traffic_medium` |
| **關鍵字搜尋** | 同時搜尋活動名稱、來源、國家 |
| **重設** | 清空所有篩選（日期還原為預設 30 天、清除排序）|

### 事件分析 Tab
- **KPI 卡片（含與前期比較）**：事件總數 / 不重複使用者 / 收益 / 交易筆數，下方顯示 ▲▼ 相對前期變化率
- **每日趨勢圖**：事件量長條 + **7 日移動平均線** + 收益折線，雙 Y 軸
- **事件組成**：本區間各 `event_name` 筆數橫條
- **轉換漏斗**：瀏覽 → 加入購物車 → 結帳 → 成交，以**不重複使用者、逐階段累積**計算（轉換率必 ≤100%）
- **裝置類型圓餅圖**
- **後端分頁明細表**：每頁 20 筆；**點欄位標題即可後端排序**（作用於全部資料，非只當前頁）

### 商品分析 Tab（僅計 `purchase` 事件）
- **前五大商品類別營收**：以 EXISTS 半連接套用篩選，避免多對多 JOIN 造成營收重複加總
- **前十大熱銷商品（已售數量）**：長條 hover 顯示完整商品名（不受軸標籤截斷影響）
- **已購買商品後端分頁明細表**：已濾除整列去識別化的 `(not set)` 品項

### 新用戶分析 Tab（`first_visit` 人群 + 轉換）
- **統計卡**：新用戶數 / 已轉換人數 / 新用戶轉換率 / 平均首購天數
- **每日新增趨勢**：新增用戶長條 + 其中轉換折線（雙 Y 軸）
- **裝置分佈 / 前十來源國家 / 流量媒介組成**
- **各流量媒介的新用戶轉換率**（購買人數 / 新用戶數，樣本 ≥20 才列入）

> 此分頁需先由 `etl.py` 產生 `first_visit_users_data.parquet`；若檔案不存在，分頁會顯示提示而非報錯。

### 效能計時器
頂部右側 Badge 在每次查詢後**出現約 1 秒即自動淡出**（clientside），顯示該次 DuckDB 耗時與掃描筆數。

> **關於來源與行銷活動**：「來源」取自 `traffic_source`（有實值）；「行銷活動」取自 `traffic_source.name`，在這份去識別化範本中多為 `(organic)/(referral)/(data deleted)` 等佔位值，屬資料源限制。

---

## 排程（選用）：Airflow 月更新

[`schedule/ga4_etl_dag.py`](schedule/ga4_etl_dag.py) 是一支 Airflow DAG：每月 1 號 03:00 自動換算「上一個完整月份」的日期區間並重跑 `etl.py`（一次產出三張表）。

- 正式環境憑證以環境變數注入（`GCP_AUTH_ENV` 預留接點，建議走 Workload Identity Federation / OIDC，不落地長期金鑰）。
- Airflow 不支援 Windows 原生執行，需以 Docker 或 WSL2/Linux 部署；DAG 本身已寫好、可直接放入 Airflow 的 `dags/`。
- 單機輕量需求亦可改用 Windows 工作排程器 / cron 直接跑 `etl.py`。

---

## 技術亮點

| # | 功能 | 技術機制 |
|---|------|----------|
| 1 | **記憶體物化（DuckDB 版 MView）** | 啟動以 `CREATE TABLE` 只物化所需欄位，查詢即記憶體，較 View 快約 3 倍 |
| 2 | **跨表半連接** | 商品營收以 EXISTS 半連接套用事件級篩選，正確且不重複加總 |
| 3 | **後端分頁 + 後端排序** | DataTable `custom` 模式，`ORDER BY ... LIMIT N OFFSET M`，欄名走白名單防注入 |
| 4 | **轉換漏斗 + 與前期比較** | 單次掃描算出漏斗各階段不重複使用者與轉換率；KPI 自動比對等長前期 |
| 5 | **新用戶轉換分析** | ETL 階段以 window function 取首訪事件、LEFT JOIN 首購，落地轉換旅程標記 |
| 6 | **SQL 公版化 + 可排程 ETL** | 查詢抽成 `sql/*.sql`、以參數注入；附 Airflow 月排程 DAG |

---

## 檔案結構

```
dashboard-demo/
├── app.py                  # Dash 儀表板主程式（含記憶體物化、三個分頁、後端分頁/排序）
├── etl.py                  # BigQuery → Parquet ETL（讀 sql/ 公版、一次產三表、含進度條）
├── sql/                    # ETL 的 SQL 公版檔（${START}/${END} 參數化）
│   ├── events.sql
│   ├── products.sql
│   └── first_visit.sql
├── schedule/               # 排程
│   └── ga4_etl_dag.py      # Airflow 月排程 DAG（憑證以環境變數注入）
├── requirements.txt
├── .gitignore
├── README.md
└── doc/                    # 參考文件（不影響執行）
    ├── Discussion_SDD.md
    ├── event.sql           # 早期單表參考 SQL（正式版見 sql/events.sql）
    ├── product.sql         # 早期單表參考 SQL（正式版見 sql/products.sql）
    └── duckdb-benchmark/   # DuckDB vs SQLite 效能基準
        ├── README.md
        ├── timings.png
        ├── timings.csv
        ├── duckdb_compare.py
        └── benchmark_and_plot.py
```

> 三個 `*.parquet` 由 `etl.py` 產生於專案根目錄，已列入 `.gitignore`，不會推上 GitHub；需要時重跑 `etl.py`。
