# Web Traffic Dashboard Demo

> GA4 公開電商樣本資料的網站流量分析儀表板 · DuckDB + Plotly Dash

一個以 Google GA4 公開電商樣本（約 430 萬筆事件）打造的互動式分析儀表板：用 DuckDB 直接查詢 Parquet，前端以 Plotly Dash 呈現 KPI、趨勢、轉換漏斗與後端分頁明細。

## 架構總覽

```
BigQuery 公開資料集 ──(etl.py / pandas-gbq)──▶ events_data.parquet
ga4_obfuscated_sample_ecommerce                products_data.parquet
                                                      │
                                                      ▼
                                          DuckDB (記憶體) 直接查詢 Parquet
                                                      │
                                                      ▼
                                          Plotly Dash (FLATLY) 儀表板
                                          http://127.0.0.1:8050
```

- **無需資料庫伺服器**：DuckDB 直接掃描本地 Parquet，免架 DB、免匯入。
- **資料不進 Pandas 常駐**：App 啟動只開 DuckDB 連線與 View，查詢即掃即回，毫秒級響應。

---

## 前置需求

- Python 3.9+
- Google Cloud 帳號（查詢 BigQuery 公開資料集，幾乎免費）

```bash
pip install -r requirements.txt
```

---

## 快速開始

### 步驟一：ETL — 從 BigQuery 下載資料（一次性）

```bash
# 單日快速測試（預設 2021-01-01，約 10–20 萬筆）
python etl.py

# 完整區間（約 430 萬筆事件 + 對應商品明細）
python etl.py 20201101 20210131
```

**執行時間參考：**

| 模式 | 日期範圍 | 預估筆數 | 預估時間 |
|------|----------|----------|----------|
| 快速測試 | 單日（預設） | ~10–20 萬筆 | **1–3 分鐘** |
| 完整區間 | 3 個月 | ~430 萬筆事件 + 商品明細 | **15–30 分鐘** |

> 時間受網速與 BigQuery slot 負載影響，首次執行包含 OAuth 授權流程會多 1–2 分鐘。

**OAuth 授權流程（第一次執行）：**
1. 終端機執行後會自動開啟瀏覽器
2. 選擇 Google 帳號並授權
3. 授權完成後 ETL 自動繼續，後續執行不再重複要求

**產出檔案：**
- `events_data.parquet`：事件主表（Snappy 壓縮）
- `products_data.parquet`：商品明細表（Snappy 壓縮）

> 這兩個 Parquet 檔已列入 `.gitignore`，不會推上 GitHub。需重新取得時重跑 `etl.py`。

---

### 步驟二：啟動儀表板

```bash
python app.py
```

開啟瀏覽器前往 **http://127.0.0.1:8050**

啟動時間 < 5 秒（DuckDB 建立 View，不載入記憶體）。

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

> 延伸驗證：本專案附帶的 [`doc/duckdb-benchmark/`](doc/duckdb-benchmark/) 進一步比較了 **DuckDB（欄式）vs SQLite（列式）** 在千萬～億級資料上的 OLAP 聚合效能，DuckDB 快 **50–110 倍**（詳見該資料夾 README 與 [`timings.png`](doc/duckdb-benchmark/timings.png)）。本儀表板「Parquet + DuckDB」的組合即建立在這個結論之上。

---

## 功能說明

### 左側控制面板
| 控制項 | 說明 |
|--------|------|
| **日期區間** | `DatePickerRange`，所有圖表與表格依時間窗動態切片；**預設最近 30 天**，讓「與前期比較」一開啟就有對照基準 |
| **事件名稱** | 多選，對應 GA4 `event_name`（page_view、purchase…）|
| **國家** | 多選，對應 `geo.country` |
| **商品類別** | 多選，對應 `item_category`（僅作用於商品分析頁）|
| 流量媒介 | 多選下拉，對應 GA4 `traffic_medium` |
| 關鍵字搜尋 | 同時搜尋活動名稱、來源、國家 |
| 重設按鈕 | 清空所有篩選條件（日期還原為預設 30 天）|

### 事件分析 Tab
- **KPI 卡片（含與前期比較）**：事件總數 / 不重複使用者 / 收益 / 交易筆數，下方顯示 ▲▼ 相對前一個對照區間的變化率
- **每日趨勢圖**：事件量長條 + **7 日移動平均線** + 收益折線，雙 Y 軸
- **事件組成**：本區間各 `event_name` 筆數橫條，一眼看出 page_view / purchase 等占比
- **轉換漏斗**：瀏覽商品 → 加入購物車 → 進入結帳 → 完成交易，以**不重複使用者、逐階段累積**計算（保證單調遞減，轉換率必 ≤100%）
- **裝置類型圓餅圖**
- **後端分頁明細表**：每頁 20 筆；欄位含媒介、**來源**、行銷活動、交易 ID、收益

### 商品分析 Tab（僅計 `purchase` 事件）
- **前五大商品類別營收**：以 EXISTS 半連接套用篩選，避免多對多 JOIN 造成營收重複加總
- **前十大熱銷商品（已售數量）**
- **已購買商品後端分頁明細表**：已濾除整列去識別化的 `(not set)` 品項（約 2.8%）

### 效能計時器
頂部右側 Badge 在每次查詢後**出現約 1 秒即自動淡出**（clientside 控制），顯示該次 DuckDB 耗時與掃描筆數，例如 `⚡ DuckDB 查詢 743 ms · 掃描 1,188,051 筆`，不長駐畫面。

> **關於「與前期比較」**：拿「目前選的區間」對比「**等長且緊鄰其前**的區間」，並依長度自動命名——選約 30 天→「前一個月」、約 90 天→「前一季」、約 365 天→「前一年」。KPI 列下方會註明實際比較的日期。預設顯示最近 30 天，前期自然有資料；若把起始日拉到資料最早日 (2020-11-01)，前期落在資料範圍外，會顯示「無前期可比」，屬正常。
>
> **關於來源與行銷活動**：事件表的「來源」欄取自 `traffic_source`（google、(direct)…，有實值）；「行銷活動」取自 `traffic_source.name`，在這份 GA4 去識別化範本中多為 `(organic)/(referral)/(data deleted)` 等佔位值，並非真實活動名，屬資料源本身限制。

---

## 技術亮點

| # | 功能 | 技術機制 |
|---|------|----------|
| 1 | **毫秒級效能計時器** | `time.perf_counter()` 量測每次 DuckDB 掃描+運算耗時，頂部 Badge 顯示後自動淡出 |
| 2 | **跨表半連接** | 商品營收以 EXISTS 半連接套用事件級篩選，正確且不重複加總 |
| 3 | **後端分頁表** | DataTable `custom` 模式，`LIMIT N OFFSET M` 每次精準只回 20 筆 |
| 4 | **轉換漏斗 + 與前期比較** | 單次掃描算出漏斗各階段不重複使用者與轉換率；KPI 自動比對等長前期，呈現 ▲▼ 變化 |
| 5 | **日期區間切片** | 日期 picker 連動全部查詢，搭配 7 日移動平均觀察趨勢去雜訊 |

---

## 視覺設計

- 主題：**FLATLY**（深藍 `#2C3E50` / 商務藍 `#2980B9` / 岩石灰 `#95A5A6`）
- 佈局：頂部 Banner（內嵌效能計時器）→ 左控制面板 (md=3) + 右核心區 (md=9) → 雙 Tab 分頁
- Bootstrap Icons、卡片陰影、奇偶列底色

---

## 檔案結構

```
dashboard-demo/
├── app.py                  # Dash 儀表板主程式
├── etl.py                  # BigQuery → Parquet ETL（含 tqdm 進度條）
├── requirements.txt        # 套件清單
├── .gitignore
├── README.md
└── doc/                    # 參考文件（不影響執行）
    ├── Discussion_SDD.md   # 設計討論紀錄
    ├── event.sql           # 事件表原始 SQL 查詢
    ├── product.sql         # 商品表原始 SQL 查詢
    └── duckdb-benchmark/   # DuckDB vs SQLite 效能基準（佐證選用 DuckDB+Parquet）
        ├── README.md
        ├── timings.png     # 效能比較圖
        ├── timings.csv
        ├── duckdb_compare.py
        └── benchmark_and_plot.py
```

> `events_data.parquet` / `products_data.parquet` 由 `etl.py` 產生，存於專案根目錄，已列入 `.gitignore`。
