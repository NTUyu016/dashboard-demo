# 軟體設計規格書 (SDD) v2.0
## 專案名稱：地端千萬級採購數據流向與效能驗證儀表板 (DuckDB + Plotly Dash)

---

## 1. 專案背景與戰略定位 (Project Context)

### 1.1 核心戰術：雙關語類比 (Analogy Strategy)
為符合半導體晶圓廠高規格資安要求，本專案 100% 揚棄真實廠內數據。Demo 採用 Google 官方公開之千萬級 GA4 虛擬電商大數據（約 430 萬筆），並於前端界面與簡報中，同構類比為「廠內物料與採購生命週期日誌（PO Lifecycle Log）」：
* `event_name` (用戶行為事件) ➔ 類比為 `PR_Created` (請購) ➔ `PO_Approved` (採購核准) ➔ `Good_Received` (收料) 的對帳單節點。
* `geo.country` (流量地理分佈) ➔ 類比為「全球半導體材料/設備供應商採購支出佔比（US/Japan/Europe）」。
* `device.category` (終端類別) ➔ 類比為「晶圓廠區/設備終端機分類 (Fab 12 / Fab 18 / 測試廠)」。

### 1.2 部署約束 (Deployment Constraints)
* **環境**：Python 3.10+ (向下相容至 Python 3.9，支援離線 Wheel 包安裝)。
* **資安合規**：100% On-premises 地端架構，無須架設外部 Database Server，無任何外部 API 呼叫，完全適用於廠區機房之實體隔離環境（Air-gapped Environment）。

---

## 2. 資料工程與資料庫設計 (Data Infrastructure)

資料源落地為兩個高壓縮比之本地 Parquet 檔案（由 BigQuery 匯出並以 Snappy 壓縮平整化）：

### 2.1 檔案 A：事件維度主表 (`events_data.parquet`)
* **核心欄位**：`event_date` (DATE), `event_name` (VARCHAR), `user_pseudo_id` (VARCHAR), `country` (VARCHAR), `device_type` (VARCHAR), `campaign_name` (VARCHAR), `total_purchase_revenue` (DOUBLE).

### 2.2 檔案 B：產品維度明細表 (`products_data.parquet`)
* **核心欄位**：`user_pseudo_id` (VARCHAR), `event_datetime` (TIMESTAMP), `item_id` (VARCHAR, 類比採購料號), `item_name` (VARCHAR), `item_category` (VARCHAR, 類比經費科目), `item_price` (DOUBLE), `item_quantity` (BIGINT).
* **關聯鍵**：透過 `user_pseudo_id` 與 `event_datetime` 進行兩表之跨檔案高動態 `JOIN` 查詢。

---

## 3. UI/UX 與黑科技功能模組 (Frontend & Features)

### 3.1 視覺規範 (Aesthetics)
* **主題**：Dash Bootstrap Components `FLATLY` 主題（簡潔高質感商務風格，主色調為深藍、白、岩石灰）。
* **佈局 (Layout)**：
    * **頂部 Banner**：系統主標題，並內嵌 **【黑科技效能計時器看板】**。
    * **左側控制面板 (md=4)**：包含 `dcc.Dropdown` (採購活動/行銷管道篩選) 與 關鍵字搜尋框。
    * **右側核心圖表 (md=8)**：包含 KPI 卡片組、跨表聯查之高階圖表、以及時序趨勢。
    * **底部全寬區 (md=12)**：千萬級後端分頁表格。

### 3.2 三大黑科技亮點功能 (Killer Features for Demo)

#### 亮點一：毫秒級效能計時器 (Performance Benchmarker)
* **機制**：每次 Callback 觸發 DuckDB 查詢時，利用 Python `time.perf_counter()` 計算硬碟掃描與 SQL 運算時間。
* **UI 呈現**：於頂部以高亮 Badge 顯示：`"DuckDB 隔空檢索 4,300,000 筆事件耗時: 0.0342 秒"`。用絕對的速度震撼習慣傳統 RDBMS MV 查詢的主管。

#### 亮點二：跨表動態聯查圖表 (Dynamic Sub-Second JOIN Chart)
* **機制**：當使用者篩選特定廠區/管道時，DuckDB 直接在記憶體中對兩個 Parquet 檔案下達 `JOIN` 指令。
* **圖表呈現**：計算「當前篩選條件下，採購金額/經費最高的前五大產品科目」，並以 **Plotly 水平長條圖 (Horizontal Bar Chart)** 呈現，確保長字串料號不被遮擋。

#### 亮點三：千萬級後端分頁表格 (Server-side Pagination DataTable)
* **機制**：拒絕一次性加載百萬行資料導致網頁崩潰。採用 Dash DataTable `custom` 模式。
* **邏輯**：當用戶切換分頁或點擊下一頁時，前端傳入 `page_current` 與 `page_size`，後端對 DuckDB 下達帶有 `LIMIT {page_size} OFFSET {page_current * page_size}` 的 SQL 語法，每次精準只抓 20 筆資料回傳前端，實現零卡頓的原始流水帳對帳表。

---

## 4. 響應式狀態管理與 Callback 規範

開發 Agent 必須遵循以下架構撰寫 Python Callback，避免記憶體洩漏與重複讀取硬碟：

```python
import dash
from dash import dcc, html, Input, Output, State
import duckdb
import time

# 全域僅宣告 DuckDB 連線，不將資料載入 Pandas DataFrame
con = duckdb.connect()

# 核心數據過濾與分頁 Callback 邏輯範例
# Agent 實作時須擴充 Output 以同步刷新效能計時器與多張圖表