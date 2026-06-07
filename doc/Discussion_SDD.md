# 軟體設計規格書 (SDD)
## 專案名稱：Web Traffic Dashboard Demo (DuckDB + Plotly Dash)

---

## 1. 專案背景 (Project Context)

以 Google 官方公開的 GA4 電商樣本資料集（`bigquery-public-data.ga4_obfuscated_sample_ecommerce`，約 430 萬筆事件）打造一個互動式網站流量分析儀表板，展示：

- 用 **DuckDB 直接查詢 Parquet** 的查詢效能（免架資料庫、免匯入）。
- 用 **Plotly Dash** 做出可互動、可篩選、後端分頁的分析介面。

### 1.1 環境
- Python 3.9+
- 套件：duckdb / plotly / dash / dash-bootstrap-components（前端），pandas-gbq / pyarrow / tqdm（ETL）。

---

## 2. 資料工程與資料設計 (Data Infrastructure)

ETL（`etl.py`）從 BigQuery 取兩張表，落地成本地 Parquet（Snappy 壓縮）：

### 2.1 事件主表 (`events_data.parquet`)
- 核心欄位：`event_date` (DATE), `event_datetime` (TIMESTAMP), `event_name` (VARCHAR), `user_pseudo_id` (VARCHAR), `country` (VARCHAR), `device_type` (VARCHAR), `traffic_medium` / `traffic_source` / `campaign_name` (VARCHAR), `transaction_id` (VARCHAR), `total_purchase_revenue` (DOUBLE)。

### 2.2 商品明細表 (`products_data.parquet`)
- 核心欄位：`user_pseudo_id` (VARCHAR), `event_datetime` (TIMESTAMP), `event_name`, `item_id` / `item_name` / `item_brand` / `item_category` (VARCHAR), `item_price` (DOUBLE), `item_quantity` (BIGINT), `item_total_revenue` (DOUBLE)。
- 關聯鍵：以 `user_pseudo_id` + `event_datetime` 對兩表做跨檔 `JOIN`。

> 為什麼用 Parquet 而非 CSV：欄式儲存只讀用到的欄位、內建壓縮與型別。實測同資料下檔案小約 8 倍、聚合查詢快約 10 倍（詳見根目錄 README 與 `doc/duckdb-benchmark/`）。

---

## 3. UI/UX 與功能模組 (Frontend & Features)

### 3.1 視覺規範
- 主題：Dash Bootstrap Components `FLATLY`（深藍 / 商務藍 / 岩石灰）。
- 佈局：
    - **頂部 Banner**：標題 + 內嵌效能計時器 Badge（查詢後出現約 1 秒自動淡出）。
    - **左側控制面板 (md=3)**：日期區間、事件名稱、國家、商品類別、流量媒介、關鍵字。
    - **右側核心區 (md=9)**：KPI 卡片組 + 雙 Tab（事件分析 / 商品分析），各 Tab 內含圖表與後端分頁明細表。

### 3.2 功能亮點
1. **毫秒級效能計時器**：每次 Callback 用 `time.perf_counter()` 量 DuckDB 掃描+運算耗時，Badge 顯示後淡出。
2. **轉換漏斗（不重複使用者、逐階段累積）**：以 `view_item → add_to_cart → begin_checkout → purchase` 計算各階段不重複使用者，數學上保證單調遞減、轉換率 ≤100%。
3. **跨表半連接營收**：商品營收以 `EXISTS` 半連接套用事件級篩選，避免 events×products 多對多 `JOIN` 重複加總。
4. **與前期比較**：KPI 自動對比「等長且緊鄰其前」的區間，依長度命名前一週/月/季/年，顯示 ▲▼ 變化率。
5. **後端分頁表格**：DataTable `custom` 模式，前端傳 `page_current`/`page_size`，後端以 `LIMIT/OFFSET` 每次只回 20 筆，避免一次載入百萬列。

---

## 4. Callback 與狀態管理規範

- **每個 Callback 開獨立 DuckDB 連線**（`duckdb.connect()` 後建立 view），查完即關，避免多執行緒共用同一連線的競態。
- **資料不進 Pandas 常駐**：啟動只建立 view 與少量下拉選項，查詢即掃即回。
- **篩選條件集中組裝**：所有事件級查詢共用 `build_filter()` 產生的 `WHERE`；商品頁另用 `build_product_where()`（EXISTS 半連接）。
