# -*- coding: utf-8 -*-
"""
GA4 電商流量分析儀表板 (DuckDB + Plotly Dash)
資料源：Google GA4 obfuscated sample ecommerce (BigQuery 公開資料集)
主題：Claude / Anthropic 風格 (cream canvas + coral，見 assets/claude.css 與 design.md)

啟動：
    python app.py
    瀏覽器開啟 http://127.0.0.1:8050
"""
import os
import time
from datetime import datetime, timedelta

import duckdb
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, dash_table, dcc, html
import dash_bootstrap_components as dbc

import warehouse

# ──────────────────────────────────────────────────────────────
# 0. 資料來源 & DuckDB 連線
# ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_PARQUET   = os.path.join(BASE_DIR, "events_data.parquet").replace("\\", "/")
PRODUCTS_PARQUET = os.path.join(BASE_DIR, "products_data.parquet").replace("\\", "/")
NEWUSER_PARQUET  = os.path.join(BASE_DIR, "first_visit_users_data.parquet").replace("\\", "/")

E = EVENTS_PARQUET
P = PRODUCTS_PARQUET
N = NEWUSER_PARQUET


# ── 效能設計：開啟「ETL 預先物化好的持久化 DuckDB 檔」(唯讀) ──────
# 物化邏輯已移到 ETL 階段 (見 warehouse.build_warehouse)，產出 ga4.duckdb。
# app 啟動只「唯讀開檔」，不再每次重建 1.4GB → 啟動接近即時。
#   - 唯讀檔可被多進程 (多 gunicorn worker) 同時開啟、共享 OS page cache，
#     不會每個進程各自重建、記憶體翻倍。
#   - 若 ga4.duckdb 不存在 (例如只跑過舊版 etl 只有 parquet)，這裡一次性建好再開。
DB_PATH = os.path.join(BASE_DIR, warehouse.DB_FILENAME)
if not os.path.exists(DB_PATH):
    print(f"[app] 未找到 {warehouse.DB_FILENAME}，從 Parquet 一次性建立...")
    warehouse.build_warehouse(DB_PATH, E, P, N)

CON = duckdb.connect(DB_PATH, read_only=True)


def db():
    """回傳共用唯讀連線的執行緒安全游標。

    資料已由 ETL 物化進 ga4.duckdb，cursor() 衍生的游標可在 Dash 多執行緒
    callback 下併發查詢；唯讀模式允許多進程同時開啟同一檔。
    """
    return CON.cursor()


TOTAL_EVENTS = CON.execute("SELECT count(*) FROM events").fetchone()[0]
MEDIUM_OPTIONS = [
    {"label": (m if m and m.strip() else "(direct)"), "value": (m or "")}
    for (m,) in CON.execute(
        "SELECT DISTINCT traffic_medium FROM events ORDER BY 1 NULLS FIRST"
    ).fetchall()
]
# 國家 / 事件名稱 / 商品類別 下拉選項 (依出現頻率排序)
COUNTRY_OPTIONS = [
    {"label": c, "value": c}
    for (c,) in CON.execute(
        "SELECT country FROM events WHERE country IS NOT NULL "
        "GROUP BY 1 ORDER BY count(*) DESC"
    ).fetchall()
]
EVENTNAME_OPTIONS = [
    {"label": n, "value": n}
    for (n,) in CON.execute(
        "SELECT event_name FROM events GROUP BY 1 ORDER BY count(*) DESC"
    ).fetchall()
]
# 類別選項必須與「商品分析頁實際篩選的資料」對齊：該頁只算 purchase 事件，
# 而 purchase 的 item_category 是短名 (Apparel)，與瀏覽事件的路徑名
# (Home/Apparel/.../) 是兩套值。若用全部事件建選單會選到路徑名 → 篩 purchase 為 0 筆。
CATEGORY_OPTIONS = [
    {"label": c, "value": c}
    for (c,) in CON.execute(
        "SELECT item_category FROM products "
        "WHERE event_name = 'purchase' "
        "AND item_category IS NOT NULL AND item_category <> '' "
        "GROUP BY 1 ORDER BY count(*) DESC"
    ).fetchall()
]
MIN_DATE, MAX_DATE = CON.execute(
    "SELECT min(event_date), max(event_date) FROM events"
).fetchone()

# ── 新用戶 (first_visit) 表：可能尚未產出，故檢查倉儲中是否有此表 ──
NEWUSER_AVAILABLE = CON.execute(
    "SELECT count(*) FROM information_schema.tables WHERE table_name = 'newusers'"
).fetchone()[0] > 0

# 預設只看最近 30 天，讓「與前期比較」一開啟就有對照基準
DEFAULT_END = MAX_DATE
DEFAULT_START = max(MIN_DATE, MAX_DATE - timedelta(days=29))

# 轉換漏斗階段：瀏覽→加入→結帳→成交
FUNNEL_STAGES = [
    ("view_item",      "瀏覽商品"),
    ("add_to_cart",    "加入購物車"),
    ("begin_checkout", "進入結帳"),
    ("purchase",       "完成交易"),
]

# ──────────────────────────────────────────────────────────────
# 1. 查詢輔助
# ──────────────────────────────────────────────────────────────
def build_filter(mediums, keyword, date_start=None, date_end=None,
                 countries=None, event_names=None):
    """組出針對 events 別名 e 的 WHERE 條件 (所有查詢共用)。"""
    clauses, params = [], []
    if mediums:
        placeholders = ",".join(["?"] * len(mediums))
        clauses.append(f"COALESCE(e.traffic_medium,'') IN ({placeholders})")
        params.extend(mediums)
    if countries:
        placeholders = ",".join(["?"] * len(countries))
        clauses.append(f"e.country IN ({placeholders})")
        params.extend(countries)
    if event_names:
        placeholders = ",".join(["?"] * len(event_names))
        clauses.append(f"e.event_name IN ({placeholders})")
        params.extend(event_names)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        clauses.append(
            "(e.campaign_name ILIKE ? OR e.traffic_source ILIKE ? OR e.country ILIKE ?)"
        )
        params.extend([kw, kw, kw])
    if date_start:
        clauses.append("e.event_date >= ?")
        params.append(date_start)
    if date_end:
        clauses.append("e.event_date <= ?")
        params.append(date_end)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def build_newuser_where(countries, mediums, keyword, date_start=None, date_end=None):
    """組出針對 newusers 別名 n 的 WHERE 條件（新用戶分頁專用）。

    新用戶表一列即一位使用者、且為彙總後的維度，故只套用對新用戶有意義的
    篩選：日期(以首訪日 first_visit_date)、國家、流量媒介、關鍵字。
    事件名稱 / 商品類別 與新用戶人群無關，不套用。
    """
    clauses, params = [], []
    if countries:
        ph = ",".join(["?"] * len(countries))
        clauses.append(f"n.country IN ({ph})")
        params.extend(countries)
    if mediums:
        ph = ",".join(["?"] * len(mediums))
        clauses.append(f"COALESCE(n.traffic_medium,'') IN ({ph})")
        params.extend(mediums)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        clauses.append(
            "(n.campaign_name ILIKE ? OR n.traffic_source ILIKE ? OR n.country ILIKE ?)"
        )
        params.extend([kw, kw, kw])
    if date_start:
        clauses.append("n.first_visit_date >= ?")
        params.append(date_start)
    if date_end:
        clauses.append("n.first_visit_date <= ?")
        params.append(date_end)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _as_date(v):
    """DatePickerRange 回傳字串，預設值則為 date 物件，統一轉成 date。"""
    if v is None:
        return None
    if isinstance(v, str):
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    return v


def previous_period(date_start, date_end):
    """回傳與目前區間等長、緊鄰其前的『前期』日期區間，供與前期比較使用。"""
    if not date_start or not date_end:
        return None, None
    ds = _as_date(date_start)
    de = _as_date(date_end)
    length = (de - ds).days
    prev_end = ds - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length)
    return prev_start.isoformat(), prev_end.isoformat()


def period_label(date_start, date_end):
    """依目前區間長度，給『前期』一個白話名稱 (前一週/月/季/年)。"""
    if not date_start or not date_end:
        return "前期"
    length = (_as_date(date_end) - _as_date(date_start)).days + 1  # 含頭含尾天數
    if 6 <= length <= 8:
        return "前一週"
    if 26 <= length <= 31:
        return "前一個月"
    if 84 <= length <= 95:
        return "前一季"
    if 358 <= length <= 372:
        return "前一年"
    return f"前 {length} 天"


def delta_badge(cur, prev, label="前期"):
    """產生與前期比較的變化文字 (▲/▼ 百分比)，前期無資料時顯示『—』。"""
    if not prev:
        return html.Span("— 無前期可比", className="text-muted")
    pct = (cur - prev) / prev * 100
    up = pct >= 0
    return html.Span(
        f"{'▲' if up else '▼'} {abs(pct):.1f}% vs {label}",
        style={"color": TEAL if up else "#c64545", "fontWeight": "600"},
    )


# ──────────────────────────────────────────────────────────────
# 2. 視覺元件 — Claude / Anthropic 風格調色盤 (依 design.md)
#    cream canvas + coral accent + dark navy。NAVY/BLUE 沿用變數名但重新指色，
#    BLUE 改指品牌 coral，NAVY 改指 dark surface ink，減少散落各圖的改動。
# ──────────────────────────────────────────────────────────────
INK    = "#141413"   # 標題/深色表頭
NAVY   = "#181715"   # dark surface
CORAL  = "#cc785c"   # 品牌主色 (primary)
BLUE   = "#cc785c"   # (相容別名) 原藍色一律改 coral
TEAL   = "#5db8a6"   # 次要 accent
AMBER  = "#e8a55a"   # 第三 accent
CANVAS = "#faf9f5"   # cream 底
PAPER  = "rgba(0,0,0,0)"  # 圖表透明底，露出 cream 卡片

# 效能 Badge 樣式：出現約 1 秒後快速淡出 (透過 opacity 切換)
BADGE_STYLE = {"fontSize": "1rem", "fontWeight": "600",
               "whiteSpace": "nowrap", "transition": "opacity .4s"}

# 效能 Badge 樣式：出現約 1 秒後快速淡出 (透過 opacity 切換)
BADGE_STYLE = {"fontSize": "1rem", "fontWeight": "600",
               "whiteSpace": "nowrap", "transition": "opacity .4s"}


def kpi_card(card_id, title, icon, color):
    return dbc.Card(
        dbc.CardBody([
            html.Div(
                [html.I(className=f"bi {icon} me-2"), html.Span(title)],
                className="text-muted small fw-bold",
            ),
            html.H3(id=card_id, className="mt-2 mb-1 fw-bold", style={"color": color}),
            html.Div(id=f"{card_id}-delta", className="small"),
        ]),
        className="shadow-sm border-0 h-100",
    )


banner = dbc.Navbar(
    dbc.Container(
        dbc.Row(
            [
                dbc.Col(
                    html.Span(
                        [html.I(className="bi bi-asterisk me-2"),
                         "GA4 電商流量分析儀表板"],
                        className="navbar-brand mb-0 h4",
                    ),
                    width="auto",
                ),
                dbc.Col(
                    dbc.Badge(
                        id="perf-badge", color="warning", text_color="dark",
                        className="px-3 py-2 shadow",
                        style={**BADGE_STYLE, "opacity": 0},
                    ),
                    width="auto",
                    className="ms-auto",
                ),
            ],
            align="center",
            className="w-100 g-0 flex-nowrap justify-content-between",
        ),
        fluid=True,
    ),
    color="light", dark=False, className="mb-3",
)

control_panel = dbc.Card(
    dbc.CardBody([
        html.H5([html.I(className="bi bi-sliders me-2"), "篩選條件"],
                className="card-title"),
        html.Hr(),
        html.Label("日期區間 (Date Range)", className="fw-bold small"),
        html.Div(
            dcc.DatePickerRange(
                id="date-range",
                min_date_allowed=MIN_DATE,
                max_date_allowed=MAX_DATE,
                start_date=DEFAULT_START,
                end_date=DEFAULT_END,
                display_format="YYYY-MM-DD",
                className="mb-3 w-100",
            ),
        ),
        html.Label("事件名稱 (Event Name)", className="fw-bold small"),
        dcc.Dropdown(
            id="eventname-filter",
            options=EVENTNAME_OPTIONS,
            multi=True,
            placeholder="不限 — 例如 page_view、purchase",
            className="mb-3",
        ),
        html.Label("國家 (Country)", className="fw-bold small"),
        dcc.Dropdown(
            id="country-filter",
            options=COUNTRY_OPTIONS,
            multi=True,
            placeholder="不限 — 可複選",
            className="mb-3",
        ),
        html.Label("商品類別 (Item Category · 僅商品分析頁)", className="fw-bold small"),
        dcc.Dropdown(
            id="category-filter",
            options=CATEGORY_OPTIONS,
            multi=True,
            placeholder="不限 — 可複選",
            className="mb-3",
        ),
        html.Label("流量媒介 (Traffic Medium)", className="fw-bold small"),
        dcc.Dropdown(
            id="medium-filter",
            options=MEDIUM_OPTIONS,
            multi=True,
            placeholder="不限 — 可複選",
            className="mb-3",
        ),
        html.Label("關鍵字搜尋 (活動名稱 / 來源 / 國家)", className="fw-bold small"),
        dbc.InputGroup(
            [
                dbc.InputGroupText(html.I(className="bi bi-search")),
                dbc.Input(id="keyword",
                          placeholder="例如 google、cpc、United States",
                          debounce=True),
            ],
            className="mb-3",
        ),
        dbc.Button([html.I(className="bi bi-arrow-clockwise me-2"), "重設"],
                   id="reset-btn", color="secondary", outline=True, size="sm",
                   className="w-100"),
        html.Hr(),
        html.Div(
            [html.I(className="bi bi-hdd-stack me-2"),
             f"DuckDB on Parquet  |  共 {TOTAL_EVENTS:,} 筆事件"],
            className="text-muted small",
            style={"whiteSpace": "nowrap", "overflow": "hidden",
                   "textOverflow": "ellipsis", "fontSize": "11px"},
        ),
    ]),
    className="shadow-sm border-0 h-100",
)

# ── KPI 列 (共用) ──
kpi_row = dbc.Row(
    [
        dbc.Col(kpi_card("kpi-events",  "事件總數",       "bi-activity",   BLUE),      md=3),
        dbc.Col(kpi_card("kpi-users",   "不重複使用者",   "bi-people",     NAVY),      md=3),
        dbc.Col(kpi_card("kpi-revenue", "收益 (USD)", "bi-cash-stack", TEAL), md=3),
        dbc.Col(kpi_card("kpi-orders",  "交易筆數",       "bi-bag-check",  AMBER), md=3),
    ],
    className="g-3 mb-3",
)

# ── Tab 1：事件分析 ──
tab_events = dbc.Tab(
    label="事件分析",
    tab_id="tab-events",
    children=[
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(dbc.CardBody([
                        html.H6([html.I(className="bi bi-graph-up me-2"),
                                 "每日事件量 / 收益趨勢"],
                                className="fw-bold"),
                        dcc.Graph(id="trend-line", config={"displayModeBar": False},
                                  style={"height": "300px"}),
                    ]), className="shadow-sm border-0"),
                    md=8,
                ),
                dbc.Col(
                    dbc.Card(dbc.CardBody([
                        html.H6([html.I(className="bi bi-pie-chart me-2"),
                                 "裝置類型分佈"],
                                className="fw-bold"),
                        dcc.Graph(id="device-pie", config={"displayModeBar": False},
                                  style={"height": "300px"}),
                    ]), className="shadow-sm border-0"),
                    md=4,
                ),
            ],
            className="g-3 mb-3",
        ),
        dbc.Card(dbc.CardBody([
            html.H6([html.I(className="bi bi-list-ol me-2"),
                     "事件組成 (本區間各事件名稱筆數)"],
                    className="fw-bold"),
            dcc.Graph(id="event-comp", config={"displayModeBar": False},
                      style={"height": "320px"}),
        ]), className="shadow-sm border-0 mb-3"),
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(dbc.CardBody([
                        html.H6([html.I(className="bi bi-funnel me-2"),
                                 "轉換漏斗 (不重複使用者 · 逐階段累積)"],
                                className="fw-bold"),
                        dcc.Graph(id="funnel-chart", config={"displayModeBar": False},
                                  style={"height": "300px"}),
                    ]), className="shadow-sm border-0"),
                    md=7,
                ),
                dbc.Col(
                    dbc.Card(dbc.CardBody([
                        html.H6([html.I(className="bi bi-percent me-2"),
                                 "各階段轉換率"],
                                className="fw-bold"),
                        html.Div(id="funnel-rates", className="mt-2"),
                    ]), className="shadow-sm border-0 h-100"),
                    md=5,
                ),
            ],
            className="g-3 mb-3",
        ),
        dbc.Card(dbc.CardBody([
            html.H6([html.I(className="bi bi-table me-2"),
                     "事件明細 (後端分頁 · 每頁 20 筆)"],
                    className="fw-bold"),
            html.Div(
                [html.I(className="bi bi-info-circle me-1"),
                 "點欄位標題即可排序 (再點切換升冪/降冪)，排序作用於全部資料、非只當前頁。"],
                className="text-muted small mb-2",
            ),
            dash_table.DataTable(
                id="events-table",
                page_current=0,
                page_size=20,
                page_action="custom",
                sort_action="custom",   # 後端分頁 → 排序也走後端 (接進 SQL)
                sort_mode="single",
                columns=[
                    {"name": "日期",         "id": "event_date"},
                    {"name": "時間",         "id": "event_datetime"},
                    {"name": "事件名稱",     "id": "event_name"},
                    {"name": "國家",         "id": "country"},
                    {"name": "裝置",         "id": "device_type"},
                    {"name": "媒介",         "id": "traffic_medium"},
                    {"name": "來源",         "id": "traffic_source"},
                    {"name": "行銷活動",     "id": "campaign_name"},
                    {"name": "交易 ID",      "id": "transaction_id"},
                    {"name": "收益 (USD)", "id": "total_purchase_revenue"},
                ],
                style_as_list_view=True,
                style_header={"backgroundColor": NAVY, "color": "white", "fontWeight": "bold"},
                style_cell={
                    "fontSize": "13px", "padding": "8px",
                    "fontFamily": "Inter, sans-serif", "textAlign": "left",
                    "maxWidth": "180px", "overflow": "hidden", "textOverflow": "ellipsis",
                },
                style_data_conditional=[
                    {"if": {"row_index": "odd"}, "backgroundColor": "#f5f0e8"}
                ],
            ),
        ]), className="shadow-sm border-0"),
    ],
)

# ── Tab 2：商品分析 ──
tab_products = dbc.Tab(
    label="商品分析",
    tab_id="tab-products",
    children=[
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(dbc.CardBody([
                        html.H6([html.I(className="bi bi-bar-chart-line me-2"),
                                 "前五大商品類別營收 (已購買商品)"],
                                className="fw-bold"),
                        dcc.Graph(id="join-bar", config={"displayModeBar": False},
                                  style={"height": "300px"}),
                    ]), className="shadow-sm border-0"),
                    md=6,
                ),
                dbc.Col(
                    dbc.Card(dbc.CardBody([
                        html.H6([html.I(className="bi bi-bar-chart me-2"),
                                 "前十大熱銷商品 (已售數量)"],
                                className="fw-bold"),
                        dcc.Graph(id="top-items-bar", config={"displayModeBar": False},
                                  style={"height": "300px"}),
                    ]), className="shadow-sm border-0"),
                    md=6,
                ),
            ],
            className="g-3 mb-3",
        ),
        dbc.Card(dbc.CardBody([
            html.H6([html.I(className="bi bi-table me-2"),
                     "已購買商品明細 (後端分頁 · 每頁 20 筆)"],
                    className="fw-bold"),
            dash_table.DataTable(
                id="products-table",
                page_current=0,
                page_size=20,
                page_action="custom",
                columns=[
                    {"name": "日期",         "id": "event_date"},
                    {"name": "事件",         "id": "event_name"},
                    {"name": "商品 ID",      "id": "item_id"},
                    {"name": "商品名稱",     "id": "item_name"},
                    {"name": "品牌",         "id": "item_brand"},
                    {"name": "類別",         "id": "item_category"},
                    {"name": "單價(USD)",    "id": "item_price"},
                    {"name": "數量",         "id": "item_quantity"},
                    {"name": "小計(USD)",    "id": "item_total_revenue"},
                ],
                style_as_list_view=True,
                style_header={"backgroundColor": TEAL, "color": "white", "fontWeight": "bold"},
                style_cell={
                    "fontSize": "13px", "padding": "8px",
                    "fontFamily": "Inter, sans-serif", "textAlign": "left",
                    "maxWidth": "180px", "overflow": "hidden", "textOverflow": "ellipsis",
                },
                style_data_conditional=[
                    {"if": {"row_index": "odd"}, "backgroundColor": "#f5f0e8"}
                ],
            ),
        ]), className="shadow-sm border-0"),
    ],
)

# ── Tab 3：新用戶分析 (first_visit 人群) ──
def nu_stat(card_id, title, icon, color):
    """新用戶分頁專用的小型統計卡。"""
    return dbc.Card(
        dbc.CardBody([
            html.Div([html.I(className=f"bi {icon} me-2"), html.Span(title)],
                     className="text-muted small fw-bold"),
            html.H4(id=card_id, className="mt-2 mb-0 fw-bold", style={"color": color}),
        ]),
        className="shadow-sm border-0 h-100",
    )


_nu_unavailable = dbc.Alert(
    [html.I(className="bi bi-exclamation-triangle me-2"),
     "尚未找到新用戶資料 first_visit_users_data.parquet，請先執行 ",
     html.Code("python etl.py 20201101 20210131"), " 產生後重啟。"],
    color="warning", className="mb-0",
)

_nu_body = [
    dbc.Row(
        [
            dbc.Col(nu_stat("nu-total",     "新用戶數",      "bi-person-plus", BLUE),     md=3),
            dbc.Col(nu_stat("nu-converted", "已轉換人數",    "bi-bag-check",   TEAL), md=3),
            dbc.Col(nu_stat("nu-rate",      "新用戶轉換率",  "bi-percent",     "#c64545"), md=3),
            dbc.Col(nu_stat("nu-days",      "平均首購天數",  "bi-clock",       NAVY),      md=3),
        ],
        className="g-3 mb-3",
    ),
    dbc.Row(
        [
            dbc.Col(
                dbc.Card(dbc.CardBody([
                    html.H6([html.I(className="bi bi-graph-up-arrow me-2"),
                             "每日新增用戶 / 其中轉換"], className="fw-bold"),
                    dcc.Graph(id="nu-trend", config={"displayModeBar": False},
                              style={"height": "300px"}),
                ]), className="shadow-sm border-0"),
                md=8,
            ),
            dbc.Col(
                dbc.Card(dbc.CardBody([
                    html.H6([html.I(className="bi bi-pie-chart me-2"),
                             "新用戶裝置分佈"], className="fw-bold"),
                    dcc.Graph(id="nu-device", config={"displayModeBar": False},
                              style={"height": "300px"}),
                ]), className="shadow-sm border-0"),
                md=4,
            ),
        ],
        className="g-3 mb-3",
    ),
    dbc.Row(
        [
            dbc.Col(
                dbc.Card(dbc.CardBody([
                    html.H6([html.I(className="bi bi-globe me-2"),
                             "新用戶前十大來源國家"], className="fw-bold"),
                    dcc.Graph(id="nu-country", config={"displayModeBar": False},
                              style={"height": "300px"}),
                ]), className="shadow-sm border-0"),
                md=6,
            ),
            dbc.Col(
                dbc.Card(dbc.CardBody([
                    html.H6([html.I(className="bi bi-signpost-split me-2"),
                             "新用戶流量媒介組成"], className="fw-bold"),
                    dcc.Graph(id="nu-medium", config={"displayModeBar": False},
                              style={"height": "300px"}),
                ]), className="shadow-sm border-0"),
                md=6,
            ),
        ],
        className="g-3 mb-3",
    ),
    dbc.Card(dbc.CardBody([
        html.H6([html.I(className="bi bi-funnel me-2"),
                 "各流量媒介的新用戶轉換率 (購買人數 / 新用戶數)"], className="fw-bold"),
        dcc.Graph(id="nu-conv", config={"displayModeBar": False},
                  style={"height": "320px"}),
    ]), className="shadow-sm border-0"),
]

tab_newusers = dbc.Tab(
    label="新用戶分析",
    tab_id="tab-newusers",
    children=_nu_body if NEWUSER_AVAILABLE else [_nu_unavailable],
)


# ──────────────────────────────────────────────────────────────
# 3. App Layout
# ──────────────────────────────────────────────────────────────
app = Dash(
    __name__,
    # 中性 Bootstrap 底 + assets/claude.css 覆寫成 Claude/Anthropic 風格
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.BOOTSTRAP],
    title="GA4 Analytics Dashboard",
    suppress_callback_exceptions=True,
)
server = app.server

app.layout = dbc.Container(
    [
        banner,
        dbc.Row(
            [
                dbc.Col(control_panel, md=3),
                dbc.Col(
                    [
                        kpi_row,
                        html.Div(id="kpi-compare-note",
                                 className="text-muted small mb-3"),
                        dbc.Tabs(
                            [tab_events, tab_products, tab_newusers],
                            id="main-tabs",
                            active_tab="tab-events",
                            className="mb-3",
                        ),
                    ],
                    md=9,
                ),
            ],
            className="g-3 mb-3",
        ),
        html.Footer(
            "資料來源：Google GA4 obfuscated sample ecommerce（BigQuery 公開資料集）"
            "  ·  DuckDB + Parquet",
            className="text-center text-muted small my-4",
        ),
    ],
    fluid=True,
    style={"backgroundColor": "#faf9f5", "minHeight": "100vh"},
)


# ──────────────────────────────────────────────────────────────
# 4. Callback：KPI + 事件頁圖表 + 效能 Badge
# ──────────────────────────────────────────────────────────────
KPI_SQL = """
    SELECT count(*),
           count(DISTINCT e.user_pseudo_id),
           COALESCE(sum(e.total_purchase_revenue), 0),
           count(DISTINCT e.transaction_id)
    FROM events e {where}
"""


@app.callback(
    Output("kpi-events",   "children"),
    Output("kpi-users",    "children"),
    Output("kpi-revenue",  "children"),
    Output("kpi-orders",   "children"),
    Output("kpi-events-delta",  "children"),
    Output("kpi-users-delta",   "children"),
    Output("kpi-revenue-delta", "children"),
    Output("kpi-orders-delta",  "children"),
    Output("trend-line",   "figure"),
    Output("device-pie",   "figure"),
    Output("event-comp",   "figure"),
    Output("funnel-chart", "figure"),
    Output("funnel-rates", "children"),
    Output("kpi-compare-note", "children"),
    Output("perf-badge",   "children"),
    Output("events-table", "page_current"),
    Input("medium-filter",    "value"),
    Input("eventname-filter", "value"),
    Input("country-filter",   "value"),
    Input("keyword",          "value"),
    Input("date-range",       "start_date"),
    Input("date-range",       "end_date"),
)
def refresh_events(mediums, event_names, countries, keyword, date_start, date_end):
    t0 = time.perf_counter()
    where, params = build_filter(mediums, keyword, date_start, date_end,
                                 countries, event_names)
    con = db()

    # 本期 KPI
    kpi = con.execute(KPI_SQL.format(where=where), params).fetchone()

    # 前期 KPI (與前期比較) ── 與目前區間「等長且緊鄰其前」的一段
    prev_start, prev_end = previous_period(date_start, date_end)
    plabel = period_label(date_start, date_end)
    if prev_start:
        where_p, params_p = build_filter(mediums, keyword, prev_start, prev_end,
                                         countries, event_names)
        kpi_p = con.execute(KPI_SQL.format(where=where_p), params_p).fetchone()
        if kpi_p[0]:
            compare_note = (
                f"📊 KPI 下方的變化＝本區間 vs 「{plabel}」"
                f"（{prev_start} ~ {prev_end}，與目前區間等長緊鄰）"
            )
        else:
            compare_note = (
                f"📊 「{plabel}」（{prev_start} ~ {prev_end}）落在資料範圍外，"
                f"故顯示「無前期可比」。把日期區間調短即可看到與前期的比較。"
            )
    else:
        kpi_p = (None, None, None, None)
        compare_note = "📊 尚未選定日期區間。"

    # 時序趨勢 (+ 7 日移動平均)
    trend = con.execute(
        f"""
        SELECT e.event_date AS d,
               count(*) AS events,
               COALESCE(sum(e.total_purchase_revenue), 0) AS revenue
        FROM events e {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).fetchdf()

    # 裝置分佈
    device = con.execute(
        f"""
        SELECT COALESCE(e.device_type, 'unknown') AS device, count(*) AS cnt
        FROM events e {where}
        GROUP BY 1 ORDER BY cnt DESC
        """,
        params,
    ).fetchdf()

    # 事件組成 (各事件名稱筆數)
    comp = con.execute(
        f"""
        SELECT e.event_name AS name, count(*) AS cnt
        FROM events e {where}
        GROUP BY 1 ORDER BY cnt DESC
        """,
        params,
    ).fetchdf()

    # ── 轉換漏斗：不重複使用者、逐階段累積 (保證單調遞減，故不套用事件名稱篩選) ──
    fwhere, fparams = build_filter(mediums, keyword, date_start, date_end,
                                   countries, None)
    funnel = con.execute(
        f"""
        WITH u AS (
            SELECT e.user_pseudo_id,
                   max(CASE WHEN e.event_name='view_item'      THEN 1 ELSE 0 END) AS s1,
                   max(CASE WHEN e.event_name='add_to_cart'    THEN 1 ELSE 0 END) AS s2,
                   max(CASE WHEN e.event_name='begin_checkout' THEN 1 ELSE 0 END) AS s3,
                   max(CASE WHEN e.event_name='purchase'       THEN 1 ELSE 0 END) AS s4
            FROM events e {fwhere}
            GROUP BY 1
        )
        SELECT COALESCE(sum(s1),0), COALESCE(sum(s1*s2),0),
               COALESCE(sum(s1*s2*s3),0), COALESCE(sum(s1*s2*s3*s4),0)
        FROM u
        """,
        fparams,
    ).fetchone()
    stage_vals = [int(v) for v in funnel]
    stage_labels = [lbl for _, lbl in FUNNEL_STAGES]

    elapsed = time.perf_counter() - t0

    # 事件組成橫條圖
    fig_comp = px.bar(comp, x="cnt", y="name", orientation="h", text="cnt",
                      color="cnt", color_continuous_scale=["#efe9de", BLUE, NAVY])
    fig_comp.update_traces(texttemplate="%{text:,}", textposition="outside",
                           textfont_size=10, cliponaxis=False)
    fig_comp.update_coloraxes(showscale=False)
    fig_comp.update_layout(
        margin=dict(l=10, r=60, t=10, b=10),
        yaxis=dict(title="", autorange="reversed", tickfont_size=11),
        xaxis_title="筆數", plot_bgcolor=PAPER, paper_bgcolor=PAPER,
        font=dict(family="Inter"),
    )

    # ── 雙軸時序圖 (事件量長條 + 7日均線 + 收益折線) ──
    fig_trend = go.Figure()
    if not trend.empty:
        trend = trend.sort_values("d")
        trend["ma7"] = trend["events"].rolling(7, min_periods=1).mean()
        fig_trend.add_bar(x=trend["d"], y=trend["events"], name="事件量",
                          marker_color="#e6cabd")
        fig_trend.add_trace(go.Scatter(
            x=trend["d"], y=trend["ma7"], name="7日均線",
            mode="lines", line=dict(color=NAVY, width=2, dash="dot"),
        ))
        fig_trend.add_trace(go.Scatter(
            x=trend["d"], y=trend["revenue"],
            name="收益", mode="lines+markers",
            line=dict(color=TEAL, width=2), yaxis="y2",
        ))
    fig_trend.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor=PAPER, paper_bgcolor=PAPER, font=dict(family="Inter"),
        yaxis=dict(title="事件量"),
        yaxis2=dict(title="收益 (USD)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.12, x=0),
    )

    # 裝置圓餅
    fig_pie = px.pie(device, names="device", values="cnt",
                     color_discrete_sequence=[CORAL, NAVY, TEAL, AMBER])
    fig_pie.update_traces(textposition="inside", textinfo="percent+label")
    fig_pie.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                          paper_bgcolor=PAPER, font=dict(family="Inter"),
                          showlegend=False)

    # ── 轉換漏斗圖 (不重複使用者) ──
    fig_funnel = go.Figure(go.Funnel(
        y=stage_labels, x=stage_vals,
        textinfo="value+percent initial",
        marker={"color": ["#e6cabd", "#d99a82", CORAL, "#8a4a34"]},
        connector={"line": {"color": "#D5DBDB"}},
    ))
    fig_funnel.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=PAPER, font=dict(family="Inter"),
    )

    # ── 各階段轉換率明細 (巢狀使用者，必 ≤ 100%) ──
    rate_items = []
    for i in range(1, len(stage_vals)):
        prev_v, cur_v = stage_vals[i - 1], stage_vals[i]
        r = (cur_v / prev_v * 100) if prev_v else 0
        rate_items.append(html.Div([
            html.Div([
                html.Span(f"{stage_labels[i-1]} → {stage_labels[i]}", className="small"),
                html.Span(f"{r:.1f}%", className="float-end fw-bold",
                          style={"color": BLUE}),
            ]),
            dbc.Progress(value=r, color="info", className="mb-3",
                         style={"height": "6px"}),
        ]))
    overall = (stage_vals[-1] / stage_vals[0] * 100) if stage_vals[0] else 0
    rate_items.append(html.Div([
        html.Hr(className="my-2"),
        html.Span("整體轉換率 (瀏覽→成交)", className="small fw-bold"),
        html.Span(f"{overall:.2f}%", className="float-end fw-bold",
                  style={"color": "#c64545", "fontSize": "1.1rem"}),
    ]))

    badge = f"⚡ DuckDB 查詢 {elapsed*1000:.0f} ms · 掃描 {kpi[0]:,} 筆"
    return (
        f"{kpi[0]:,}", f"{kpi[1]:,}", f"${kpi[2]:,.0f}", f"{kpi[3]:,}",
        delta_badge(kpi[0], kpi_p[0], plabel), delta_badge(kpi[1], kpi_p[1], plabel),
        delta_badge(kpi[2], kpi_p[2], plabel), delta_badge(kpi[3], kpi_p[3], plabel),
        fig_trend, fig_pie, fig_comp, fig_funnel, rate_items, compare_note,
        badge, 0,
    )


# 效能 Badge：每次更新後在瀏覽器端先顯示、約 1 秒後自動淡出 (clientside，最可靠)
app.clientside_callback(
    """
    function(children) {
        var el = document.getElementById('perf-badge');
        if (el) {
            el.style.opacity = 1;
            clearTimeout(window.__perfBadgeTimer);
            window.__perfBadgeTimer = setTimeout(function(){ el.style.opacity = 0; }, 1000);
        }
        return '';
    }
    """,
    Output("perf-badge", "title"),
    Input("perf-badge", "children"),
    prevent_initial_call=False,
)


# ──────────────────────────────────────────────────────────────
# 5. Callback：商品頁圖表
# ──────────────────────────────────────────────────────────────
def build_product_where(mediums, keyword, date_start, date_end,
                        countries, categories):
    """商品分析專用 WHERE：只計 purchase 事件，事件級篩選以 EXISTS 半連接套用，
    避免 events×products 多對多 JOIN 造成營收/數量被重複加總而暴增。"""
    pclauses = [
        "p.event_name = 'purchase'",
        "p.item_name IS NOT NULL",
        "p.item_name NOT IN ('', '(not set)')",  # 濾掉整列去識別化的垃圾品項
    ]
    pparams = []
    if date_start:
        pclauses.append("p.event_date >= ?")
        pparams.append(date_start)
    if date_end:
        pclauses.append("p.event_date <= ?")
        pparams.append(date_end)
    if categories:
        ph = ",".join(["?"] * len(categories))
        pclauses.append(f"p.item_category IN ({ph})")
        pparams += list(categories)

    eclauses, eparams = [], []
    if mediums:
        ph = ",".join(["?"] * len(mediums))
        eclauses.append(f"COALESCE(e.traffic_medium,'') IN ({ph})")
        eparams += mediums
    if countries:
        ph = ",".join(["?"] * len(countries))
        eclauses.append(f"e.country IN ({ph})")
        eparams += countries
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        eclauses.append(
            "(e.campaign_name ILIKE ? OR e.traffic_source ILIKE ? OR e.country ILIKE ?)"
        )
        eparams += [kw, kw, kw]

    exists = ""
    if eclauses:
        exists = (
            " AND EXISTS (SELECT 1 FROM events e "
            "WHERE e.user_pseudo_id = p.user_pseudo_id "
            "AND e.event_datetime = p.event_datetime AND "
            + " AND ".join(eclauses) + ")"
        )
    where = "WHERE " + " AND ".join(pclauses) + exists
    return where, pparams + eparams


def _short(s, n=22):
    """截斷過長字串，避免長條圖 y 軸標籤擠壓繪圖區。"""
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


@app.callback(
    Output("join-bar",      "figure"),
    Output("top-items-bar", "figure"),
    Output("products-table", "page_current"),
    Input("medium-filter",    "value"),
    Input("eventname-filter", "value"),
    Input("country-filter",   "value"),
    Input("category-filter",  "value"),
    Input("keyword",          "value"),
    Input("date-range",       "start_date"),
    Input("date-range",       "end_date"),
)
def refresh_products(mediums, event_names, countries, categories, keyword,
                     date_start, date_end):
    where, params = build_product_where(mediums, keyword, date_start, date_end,
                                        countries, categories)
    con = db()

    # 前五大類別營收 (僅 purchase，EXISTS 半連接套篩選)
    cat = con.execute(
        f"""
        SELECT COALESCE(NULLIF(p.item_category, ''), '(未分類)') AS category,
               sum(p.item_total_revenue) AS revenue
        FROM products p
        {where}
        GROUP BY 1
        HAVING sum(p.item_total_revenue) > 0
        ORDER BY revenue DESC LIMIT 5
        """,
        params,
    ).fetchdf()

    # 前十熱銷商品 (已售數量)
    items = con.execute(
        f"""
        SELECT COALESCE(NULLIF(p.item_name, ''), p.item_id) AS item,
               sum(p.item_quantity) AS qty
        FROM products p
        {where}
        GROUP BY 1
        HAVING sum(p.item_quantity) > 0
        ORDER BY qty DESC LIMIT 10
        """,
        params,
    ).fetchdf()

    # 類別營收橫條
    if cat.empty:
        fig_cat = go.Figure().add_annotation(text="此篩選條件下無商品資料", showarrow=False)
    else:
        cat = cat.iloc[::-1].copy()
        xmax = cat["revenue"].max()
        # y 用完整(唯一)類別名，避免不同類別截斷後撞名而被疊成一條
        fig_cat = px.bar(cat, x="revenue", y="category", orientation="h",
                         text="revenue", color="revenue",
                         custom_data=["category"],
                         color_continuous_scale=["#e6cabd", BLUE, NAVY])
        fig_cat.update_traces(texttemplate="$%{text:,.0f}", textposition="outside",
                              textfont_size=10, cliponaxis=False,
                              hovertemplate="<b>%{customdata[0]}</b><br>營收 $%{x:,.0f}<extra></extra>")
        fig_cat.update_coloraxes(showscale=False)
        fig_cat.update_xaxes(range=[0, xmax * 1.22])
        fig_cat.update_yaxes(tickmode="array", tickvals=cat["category"].tolist(),
                             ticktext=[_short(c, 34) for c in cat["category"]],
                             tickfont_size=11)
    fig_cat.update_layout(
        margin=dict(l=20, r=40, t=10, b=10),
        yaxis=dict(title="", automargin=True),
        xaxis_title="營收 (USD)",
        plot_bgcolor=PAPER, paper_bgcolor=PAPER, font=dict(family="Inter"),
    )

    # 熱銷商品橫條
    if items.empty:
        fig_items = go.Figure().add_annotation(text="此篩選條件下無商品資料", showarrow=False)
    else:
        items = items.iloc[::-1].copy()
        qmax = items["qty"].max()
        # y 用完整(唯一)商品名，截斷只作用在顯示的刻度文字，避免同名疊條
        fig_items = px.bar(items, x="qty", y="item", orientation="h",
                           text="qty", color="qty",
                           custom_data=["item"],
                           color_continuous_scale=["#cfe9e2", TEAL, "#2f7d6e"])
        # hover 用 customdata 帶完整商品名，不受 y 軸刻度截斷影響
        fig_items.update_traces(texttemplate="%{text:,}", textposition="outside",
                                textfont_size=10, cliponaxis=False,
                                hovertemplate="<b>%{customdata[0]}</b><br>數量 %{x:,}<extra></extra>")
        fig_items.update_coloraxes(showscale=False)
        fig_items.update_xaxes(range=[0, qmax * 1.18])
        fig_items.update_yaxes(tickmode="array", tickvals=items["item"].tolist(),
                               ticktext=[_short(s, 34) for s in items["item"]],
                               tickfont_size=11)
    fig_items.update_layout(
        margin=dict(l=20, r=40, t=10, b=10),
        yaxis=dict(title="", automargin=True),
        xaxis_title="數量",
        plot_bgcolor=PAPER, paper_bgcolor=PAPER, font=dict(family="Inter"),
    )

    return fig_cat, fig_items, 0


# ──────────────────────────────────────────────────────────────
# 5b. Callback：新用戶分頁 (僅在資料存在時註冊)
# ──────────────────────────────────────────────────────────────
if NEWUSER_AVAILABLE:
    @app.callback(
        Output("nu-total",     "children"),
        Output("nu-converted", "children"),
        Output("nu-rate",      "children"),
        Output("nu-days",      "children"),
        Output("nu-trend",     "figure"),
        Output("nu-device",    "figure"),
        Output("nu-country",   "figure"),
        Output("nu-medium",    "figure"),
        Output("nu-conv",      "figure"),
        Input("country-filter", "value"),
        Input("medium-filter",  "value"),
        Input("keyword",        "value"),
        Input("date-range",     "start_date"),
        Input("date-range",     "end_date"),
    )
    def refresh_newusers(countries, mediums, keyword, date_start, date_end):
        where, params = build_newuser_where(countries, mediums, keyword,
                                            date_start, date_end)
        con = db()

        # 統計卡：新用戶數 / 轉換人數 / 轉換率 / 平均首購天數
        total, converted, avg_days = con.execute(
            f"""
            SELECT count(*),
                   COALESCE(sum(CASE WHEN n.did_purchase THEN 1 ELSE 0 END), 0),
                   avg(CASE WHEN n.did_purchase AND n.days_to_first_purchase >= 0
                            THEN n.days_to_first_purchase END)
            FROM newusers n {where}
            """,
            params,
        ).fetchone()
        rate = (converted / total * 100) if total else 0
        avg_days_txt = f"{avg_days:.1f} 天" if avg_days is not None else "—"

        # 每日新增用戶 + 其中轉換數
        trend = con.execute(
            f"""
            SELECT n.first_visit_date AS d,
                   count(*) AS new_users,
                   COALESCE(sum(CASE WHEN n.did_purchase THEN 1 ELSE 0 END), 0) AS converted
            FROM newusers n {where}
            GROUP BY 1 ORDER BY 1
            """,
            params,
        ).fetchdf()

        # 裝置分佈
        device = con.execute(
            f"""
            SELECT COALESCE(n.device_type, 'unknown') AS device, count(*) AS cnt
            FROM newusers n {where}
            GROUP BY 1 ORDER BY cnt DESC
            """,
            params,
        ).fetchdf()

        # 前十來源國家
        country = con.execute(
            f"""
            SELECT COALESCE(NULLIF(n.country, ''), '(unknown)') AS country, count(*) AS cnt
            FROM newusers n {where}
            GROUP BY 1 ORDER BY cnt DESC LIMIT 10
            """,
            params,
        ).fetchdf()

        # 流量媒介組成
        medium = con.execute(
            f"""
            SELECT COALESCE(NULLIF(n.traffic_medium, ''), '(direct)') AS medium, count(*) AS cnt
            FROM newusers n {where}
            GROUP BY 1 ORDER BY cnt DESC
            """,
            params,
        ).fetchdf()

        # 各媒介轉換率 (購買人數 / 新用戶數)，僅取人數較有意義者
        conv = con.execute(
            f"""
            SELECT COALESCE(NULLIF(n.traffic_medium, ''), '(direct)') AS medium,
                   count(*) AS users,
                   COALESCE(sum(CASE WHEN n.did_purchase THEN 1 ELSE 0 END), 0) AS buyers
            FROM newusers n {where}
            GROUP BY 1 HAVING count(*) >= 20
            ORDER BY users DESC
            """,
            params,
        ).fetchdf()

        # ── 每日新增趨勢 (新增長條 + 轉換折線右軸) ──
        fig_trend = go.Figure()
        if not trend.empty:
            fig_trend.add_bar(x=trend["d"], y=trend["new_users"], name="新增用戶",
                              marker_color="#e6cabd")
            fig_trend.add_trace(go.Scatter(
                x=trend["d"], y=trend["converted"], name="其中轉換",
                mode="lines+markers", line=dict(color=TEAL, width=2), yaxis="y2",
            ))
        fig_trend.update_layout(
            margin=dict(l=10, r=10, t=10, b=10),
            plot_bgcolor=PAPER, paper_bgcolor=PAPER, font=dict(family="Inter"),
            yaxis=dict(title="新增用戶"),
            yaxis2=dict(title="轉換數", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", y=1.12, x=0),
        )

        # ── 裝置圓餅 ──
        fig_device = px.pie(device, names="device", values="cnt",
                            color_discrete_sequence=[CORAL, NAVY, TEAL, AMBER])
        fig_device.update_traces(textposition="inside", textinfo="percent+label")
        fig_device.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                                 paper_bgcolor=PAPER, font=dict(family="Inter"),
                                 showlegend=False)

        # ── 前十國家橫條 ──
        if country.empty:
            fig_country = go.Figure().add_annotation(text="此篩選條件下無新用戶", showarrow=False)
        else:
            country = country.iloc[::-1]
            fig_country = px.bar(country, x="cnt", y="country", orientation="h",
                                 text="cnt", color="cnt",
                                 color_continuous_scale=["#efe9de", BLUE, NAVY])
            fig_country.update_traces(texttemplate="%{text:,}", textposition="outside",
                                      textfont_size=10, cliponaxis=False)
            fig_country.update_coloraxes(showscale=False)
        fig_country.update_layout(
            margin=dict(l=10, r=50, t=10, b=10),
            yaxis=dict(title="", automargin=True), xaxis_title="新用戶數",
            plot_bgcolor=PAPER, paper_bgcolor=PAPER, font=dict(family="Inter"),
        )

        # ── 流量媒介組成 (圓餅) ──
        fig_medium = px.pie(medium, names="medium", values="cnt", hole=0.45,
                            color_discrete_sequence=['#e6cabd', '#cc785c', '#a9583e', '#8a4a34', '#181715'])
        fig_medium.update_traces(textposition="inside", textinfo="percent+label")
        fig_medium.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                                 paper_bgcolor=PAPER, font=dict(family="Inter"),
                                 showlegend=False)

        # ── 各媒介轉換率 ──
        if conv.empty:
            fig_conv = go.Figure().add_annotation(
                text="此篩選條件下無足夠樣本 (各媒介需 ≥ 20 位新用戶)", showarrow=False)
        else:
            conv["rate"] = conv["buyers"] / conv["users"] * 100
            conv = conv.sort_values("rate")
            fig_conv = px.bar(conv, x="rate", y="medium", orientation="h",
                              text="rate", custom_data=["users", "buyers"],
                              color="rate",
                              color_continuous_scale=["#cfe9e2", TEAL, "#2f7d6e"])
            fig_conv.update_traces(
                texttemplate="%{text:.2f}%", textposition="outside",
                textfont_size=10, cliponaxis=False,
                hovertemplate="<b>%{y}</b><br>轉換率 %{x:.2f}%"
                              "<br>新用戶 %{customdata[0]:,} · 購買 %{customdata[1]:,}<extra></extra>",
            )
            fig_conv.update_coloraxes(showscale=False)
        fig_conv.update_layout(
            margin=dict(l=10, r=50, t=10, b=10),
            yaxis=dict(title="", automargin=True), xaxis_title="轉換率 (%)",
            plot_bgcolor=PAPER, paper_bgcolor=PAPER, font=dict(family="Inter"),
        )

        return (
            f"{total:,}", f"{converted:,}", f"{rate:.2f}%", avg_days_txt,
            fig_trend, fig_device, fig_country, fig_medium, fig_conv,
        )


# ──────────────────────────────────────────────────────────────
# 6. Callback：事件分頁表
# ──────────────────────────────────────────────────────────────
# 事件明細表可排序的欄位 (= DataTable 欄位 id，亦即查詢輸出別名)
_EVENTS_TABLE_COLS = {
    "event_date", "event_datetime", "event_name", "country", "device_type",
    "traffic_medium", "traffic_source", "campaign_name", "transaction_id",
    "total_purchase_revenue",
}


def build_order_by(sort_by, default="e.event_datetime DESC"):
    """把 DataTable 的 sort_by ([{column_id, direction}]) 轉成 ORDER BY 子句。

    欄位以白名單檢核 (防注入)；排序作用在輸出別名上，與使用者所見一致。
    無排序時用預設 (最新事件在前)。
    """
    if not sort_by:
        return default
    parts = []
    for s in sort_by:
        cid = s.get("column_id")
        if cid in _EVENTS_TABLE_COLS:
            direction = "ASC" if s.get("direction") == "asc" else "DESC"
            parts.append(f'"{cid}" {direction}')
    return ", ".join(parts) if parts else default


@app.callback(
    Output("events-table", "data"),
    Output("events-table", "page_count"),
    Input("events-table",  "page_current"),
    Input("events-table",  "page_size"),
    Input("events-table",  "sort_by"),
    Input("medium-filter", "value"),
    Input("eventname-filter", "value"),
    Input("country-filter",   "value"),
    Input("keyword",       "value"),
    Input("date-range",    "start_date"),
    Input("date-range",    "end_date"),
)
def paginate_events(page_current, page_size, sort_by, mediums, event_names,
                    countries, keyword, date_start, date_end):
    page_current = page_current or 0
    where, params = build_filter(mediums, keyword, date_start, date_end,
                                 countries, event_names)
    con = db()
    total = con.execute(f"SELECT count(*) FROM events e {where}", params).fetchone()[0]
    page_count = max(1, -(-total // page_size))

    order_by = build_order_by(sort_by)
    rows = con.execute(
        f"""
        SELECT CAST(e.event_date AS VARCHAR)                      AS event_date,
               strftime(e.event_datetime, '%H:%M:%S')             AS event_datetime,
               e.event_name                                       AS event_name,
               COALESCE(e.country, '-')                           AS country,
               COALESCE(e.device_type, '-')                       AS device_type,
               COALESCE(NULLIF(e.traffic_medium, ''), '(direct)') AS traffic_medium,
               COALESCE(NULLIF(e.traffic_source, ''), '-')        AS traffic_source,
               COALESCE(NULLIF(e.campaign_name, ''), '-')         AS campaign_name,
               COALESCE(e.transaction_id, '-')                    AS transaction_id,
               ROUND(COALESCE(e.total_purchase_revenue, 0), 2)    AS total_purchase_revenue
        FROM events e {where}
        ORDER BY {order_by}
        LIMIT {int(page_size)} OFFSET {int(page_current) * int(page_size)}
        """,
        params,
    ).fetchdf()
    return rows.to_dict("records"), page_count


# ──────────────────────────────────────────────────────────────
# 7. Callback：商品分頁表
# ──────────────────────────────────────────────────────────────
@app.callback(
    Output("products-table", "data"),
    Output("products-table", "page_count"),
    Input("products-table", "page_current"),
    Input("products-table", "page_size"),
    Input("medium-filter",  "value"),
    Input("eventname-filter", "value"),
    Input("country-filter",   "value"),
    Input("category-filter",  "value"),
    Input("keyword",        "value"),
    Input("date-range",     "start_date"),
    Input("date-range",     "end_date"),
)
def paginate_products(page_current, page_size, mediums, event_names, countries,
                      categories, keyword, date_start, date_end):
    page_current = page_current or 0
    where, params = build_product_where(mediums, keyword, date_start, date_end,
                                        countries, categories)
    con = db()

    total = con.execute(
        f"SELECT count(*) FROM products p {where}", params
    ).fetchone()[0]
    page_count = max(1, -(-total // page_size))

    rows = con.execute(
        f"""
        SELECT CAST(p.event_date AS VARCHAR)                   AS event_date,
               p.event_name,
               COALESCE(p.item_id, '-')                        AS item_id,
               COALESCE(NULLIF(p.item_name,''), p.item_id)     AS item_name,
               COALESCE(p.item_brand, '-')                     AS item_brand,
               COALESCE(NULLIF(p.item_category,''), '(未分類)') AS item_category,
               ROUND(COALESCE(p.item_price, 0), 2)             AS item_price,
               COALESCE(p.item_quantity, 0)                    AS item_quantity,
               ROUND(COALESCE(p.item_total_revenue, 0), 2)     AS item_total_revenue
        FROM products p
        {where}
        ORDER BY p.event_datetime DESC
        LIMIT {int(page_size)} OFFSET {int(page_current) * int(page_size)}
        """,
        params,
    ).fetchdf()
    return rows.to_dict("records"), page_count


# ── 重設按鈕 ──
@app.callback(
    Output("medium-filter",   "value"),
    Output("eventname-filter", "value"),
    Output("country-filter",  "value"),
    Output("category-filter", "value"),
    Output("keyword",         "value"),
    Output("date-range",      "start_date"),
    Output("date-range",      "end_date"),
    Output("events-table",    "sort_by"),
    Input("reset-btn",        "n_clicks"),
    prevent_initial_call=True,
)
def reset(_):
    return None, None, None, None, "", DEFAULT_START, DEFAULT_END, []


if __name__ == "__main__":
    app.run(debug=False, port=8050)
