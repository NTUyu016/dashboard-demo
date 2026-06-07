# -*- coding: utf-8 -*-
"""
GA4 電商流量分析儀表板 (DuckDB + Plotly Dash)
資料源：Google GA4 obfuscated sample ecommerce (BigQuery 公開資料集)
主題：FLATLY

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

# ──────────────────────────────────────────────────────────────
# 0. 資料來源 & DuckDB 連線
# ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_PARQUET   = os.path.join(BASE_DIR, "events_data.parquet").replace("\\", "/")
PRODUCTS_PARQUET = os.path.join(BASE_DIR, "products_data.parquet").replace("\\", "/")

E = EVENTS_PARQUET
P = PRODUCTS_PARQUET


def db():
    """每次 callback 建立獨立連線，避免多執行緒衝突。"""
    c = duckdb.connect()
    c.execute(f"CREATE VIEW events   AS SELECT * FROM '{E}'")
    c.execute(f"CREATE VIEW products AS SELECT * FROM '{P}'")
    return c


_init = db()
TOTAL_EVENTS = _init.execute("SELECT count(*) FROM events").fetchone()[0]
MEDIUM_OPTIONS = [
    {"label": (m if m and m.strip() else "(direct)"), "value": (m or "")}
    for (m,) in _init.execute(
        "SELECT DISTINCT traffic_medium FROM events ORDER BY 1 NULLS FIRST"
    ).fetchall()
]
MIN_DATE, MAX_DATE = _init.execute(
    "SELECT min(event_date), max(event_date) FROM events"
).fetchone()
_init.close()

# 轉換漏斗階段 (對應採購生命週期：瀏覽→加入→結帳→成交)
FUNNEL_STAGES = [
    ("view_item",      "瀏覽商品"),
    ("add_to_cart",    "加入購物車"),
    ("begin_checkout", "進入結帳"),
    ("purchase",       "完成交易"),
]

# ──────────────────────────────────────────────────────────────
# 1. 查詢輔助
# ──────────────────────────────────────────────────────────────
def build_filter(mediums, keyword, date_start=None, date_end=None):
    clauses, params = [], []
    if mediums:
        placeholders = ",".join(["?"] * len(mediums))
        clauses.append(f"COALESCE(e.traffic_medium,'') IN ({placeholders})")
        params.extend(mediums)
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


def _as_date(v):
    """DatePickerRange 回傳字串，預設值則為 date 物件，統一轉成 date。"""
    if v is None:
        return None
    if isinstance(v, str):
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    return v


def previous_period(date_start, date_end):
    """回傳與目前區間等長、緊鄰其前的『前期』日期區間，供環比計算。"""
    if not date_start or not date_end:
        return None, None
    ds = _as_date(date_start)
    de = _as_date(date_end)
    length = (de - ds).days
    prev_end = ds - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length)
    return prev_start.isoformat(), prev_end.isoformat()


def delta_badge(cur, prev):
    """產生環比變化文字 (▲/▼ 百分比)，前期無資料時顯示『—』。"""
    if not prev:
        return html.Span("— 無前期可比", className="text-muted")
    pct = (cur - prev) / prev * 100
    up = pct >= 0
    return html.Span(
        f"{'▲' if up else '▼'} {abs(pct):.1f}% vs 前期",
        style={"color": "#18BC9C" if up else "#E74C3C", "fontWeight": "600"},
    )


# ──────────────────────────────────────────────────────────────
# 2. 視覺元件
# ──────────────────────────────────────────────────────────────
NAVY = "#2C3E50"
BLUE = "#2980B9"


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
                        [html.I(className="bi bi-bar-chart-fill me-2"),
                         "GA4 電商流量分析儀表板"],
                        className="navbar-brand mb-0 h4 text-white",
                    ),
                    width="auto",
                ),
                dbc.Col(
                    dbc.Badge(id="perf-badge", color="warning", text_color="dark",
                              className="p-2 fs-6 shadow"),
                    width="auto",
                    className="ms-auto",
                ),
            ],
            align="center",
            className="w-100 g-0 flex-nowrap justify-content-between",
        ),
        fluid=True,
    ),
    color="primary", dark=True, className="shadow mb-3",
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
                start_date=MIN_DATE,
                end_date=MAX_DATE,
                display_format="YYYY-MM-DD",
                className="mb-3 w-100",
            ),
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
        dbc.Col(kpi_card("kpi-revenue", "收益 (USD)", "bi-cash-stack", "#18BC9C"), md=3),
        dbc.Col(kpi_card("kpi-orders",  "交易筆數",       "bi-bag-check",  "#F39C12"), md=3),
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
        dbc.Row(
            [
                dbc.Col(
                    dbc.Card(dbc.CardBody([
                        html.H6([html.I(className="bi bi-funnel me-2"),
                                 "採購轉換漏斗 (瀏覽→加購→結帳→成交)"],
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
            dash_table.DataTable(
                id="events-table",
                page_current=0,
                page_size=20,
                page_action="custom",
                columns=[
                    {"name": "日期",         "id": "event_date"},
                    {"name": "時間",         "id": "event_datetime"},
                    {"name": "事件名稱",     "id": "event_name"},
                    {"name": "國家",         "id": "country"},
                    {"name": "裝置",         "id": "device_type"},
                    {"name": "媒介",         "id": "traffic_medium"},
                    {"name": "行銷活動",     "id": "campaign_name"},
                    {"name": "交易 ID",      "id": "transaction_id"},
                    {"name": "收益 (USD)", "id": "total_purchase_revenue"},
                ],
                style_as_list_view=True,
                style_header={"backgroundColor": NAVY, "color": "white", "fontWeight": "bold"},
                style_cell={
                    "fontSize": "13px", "padding": "8px",
                    "fontFamily": "Segoe UI, sans-serif", "textAlign": "left",
                    "maxWidth": "180px", "overflow": "hidden", "textOverflow": "ellipsis",
                },
                style_data_conditional=[
                    {"if": {"row_index": "odd"}, "backgroundColor": "#F8F9FA"}
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
                                 "前五大商品類別營收 (即時跨表 JOIN)"],
                                className="fw-bold"),
                        dcc.Graph(id="join-bar", config={"displayModeBar": False},
                                  style={"height": "300px"}),
                    ]), className="shadow-sm border-0"),
                    md=6,
                ),
                dbc.Col(
                    dbc.Card(dbc.CardBody([
                        html.H6([html.I(className="bi bi-bar-chart me-2"),
                                 "前十大熱銷商品 (數量)"],
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
                     "商品明細 (後端分頁 · 每頁 20 筆)"],
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
                style_header={"backgroundColor": "#18BC9C", "color": "white", "fontWeight": "bold"},
                style_cell={
                    "fontSize": "13px", "padding": "8px",
                    "fontFamily": "Segoe UI, sans-serif", "textAlign": "left",
                    "maxWidth": "180px", "overflow": "hidden", "textOverflow": "ellipsis",
                },
                style_data_conditional=[
                    {"if": {"row_index": "odd"}, "backgroundColor": "#F0FBF8"}
                ],
            ),
        ]), className="shadow-sm border-0"),
    ],
)

# ──────────────────────────────────────────────────────────────
# 3. App Layout
# ──────────────────────────────────────────────────────────────
app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY, dbc.icons.BOOTSTRAP],
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
                        dbc.Tabs(
                            [tab_events, tab_products],
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
            "  ·  DuckDB + Parquet  ·  100% 地端",
            className="text-center text-muted small my-4",
        ),
    ],
    fluid=True,
    style={"backgroundColor": "#ECF0F1", "minHeight": "100vh"},
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
    Output("funnel-chart", "figure"),
    Output("funnel-rates", "children"),
    Output("perf-badge",   "children"),
    Output("events-table", "page_current"),
    Input("medium-filter", "value"),
    Input("keyword",       "value"),
    Input("date-range",    "start_date"),
    Input("date-range",    "end_date"),
)
def refresh_events(mediums, keyword, date_start, date_end):
    t0 = time.perf_counter()
    where, params = build_filter(mediums, keyword, date_start, date_end)
    con = db()

    # 本期 KPI
    kpi = con.execute(KPI_SQL.format(where=where), params).fetchone()

    # 前期 KPI (環比) ── 等長且緊鄰的前一區間
    prev_start, prev_end = previous_period(date_start, date_end)
    if prev_start:
        where_p, params_p = build_filter(mediums, keyword, prev_start, prev_end)
        kpi_p = con.execute(KPI_SQL.format(where=where_p), params_p).fetchone()
    else:
        kpi_p = (None, None, None, None)

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

    # 轉換漏斗各階段事件數
    stage_keys = [k for k, _ in FUNNEL_STAGES]
    placeholders = ",".join(["?"] * len(stage_keys))
    fwhere = (where + " AND " if where else "WHERE ") + f"e.event_name IN ({placeholders})"
    frows = con.execute(
        f"SELECT e.event_name, count(*) FROM events e {fwhere} GROUP BY 1",
        params + stage_keys,
    ).fetchall()
    counts = {k: 0 for k in stage_keys}
    for name, c in frows:
        counts[name] = c
    stage_vals = [counts[k] for k in stage_keys]
    stage_labels = [lbl for _, lbl in FUNNEL_STAGES]

    elapsed = time.perf_counter() - t0

    # ── 雙軸時序圖 (事件量長條 + 7日均線 + 收益折線) ──
    fig_trend = go.Figure()
    if not trend.empty:
        trend = trend.sort_values("d")
        trend["ma7"] = trend["events"].rolling(7, min_periods=1).mean()
        fig_trend.add_bar(x=trend["d"], y=trend["events"], name="事件量",
                          marker_color="#AED6F1")
        fig_trend.add_trace(go.Scatter(
            x=trend["d"], y=trend["ma7"], name="7日均線",
            mode="lines", line=dict(color="#2C3E50", width=2, dash="dot"),
        ))
        fig_trend.add_trace(go.Scatter(
            x=trend["d"], y=trend["revenue"],
            name="收益", mode="lines+markers",
            line=dict(color="#18BC9C", width=2), yaxis="y2",
        ))
    fig_trend.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="white", paper_bgcolor="white", font=dict(family="Segoe UI"),
        yaxis=dict(title="事件量"),
        yaxis2=dict(title="收益 (USD)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.12, x=0),
    )

    # 裝置圓餅
    fig_pie = px.pie(device, names="device", values="cnt",
                     color_discrete_sequence=["#2980B9", "#2C3E50", "#18BC9C", "#F39C12"])
    fig_pie.update_traces(textposition="inside", textinfo="percent+label")
    fig_pie.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                          paper_bgcolor="white", font=dict(family="Segoe UI"),
                          showlegend=False)

    # ── 轉換漏斗圖 ──
    fig_funnel = go.Figure(go.Funnel(
        y=stage_labels, x=stage_vals,
        textinfo="value+percent initial",
        marker={"color": ["#AED6F1", "#5DADE2", "#2980B9", "#1F618D"]},
        connector={"line": {"color": "#D5DBDB"}},
    ))
    fig_funnel.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="white", font=dict(family="Segoe UI"),
    )

    # ── 各階段轉換率明細 ──
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
                  style={"color": "#E74C3C", "fontSize": "1.1rem"}),
    ]))

    badge = f"DuckDB 掃描 {TOTAL_EVENTS:,} 筆事件耗時：{elapsed*1000:.1f} 毫秒"
    return (
        f"{kpi[0]:,}", f"{kpi[1]:,}", f"${kpi[2]:,.0f}", f"{kpi[3]:,}",
        delta_badge(kpi[0], kpi_p[0]), delta_badge(kpi[1], kpi_p[1]),
        delta_badge(kpi[2], kpi_p[2]), delta_badge(kpi[3], kpi_p[3]),
        fig_trend, fig_pie, fig_funnel, rate_items, badge, 0,
    )


# ──────────────────────────────────────────────────────────────
# 5. Callback：商品頁圖表
# ──────────────────────────────────────────────────────────────
@app.callback(
    Output("join-bar",      "figure"),
    Output("top-items-bar", "figure"),
    Output("products-table", "page_current"),
    Input("medium-filter",  "value"),
    Input("keyword",        "value"),
    Input("date-range",     "start_date"),
    Input("date-range",     "end_date"),
)
def refresh_products(mediums, keyword, date_start, date_end):
    where, params = build_filter(mediums, keyword, date_start, date_end)
    con = db()

    # 前五大類別營收 (JOIN)
    cat = con.execute(
        f"""
        SELECT COALESCE(NULLIF(p.item_category, ''), '(未分類)') AS category,
               sum(p.item_total_revenue) AS revenue
        FROM products p
        JOIN events e ON p.user_pseudo_id = e.user_pseudo_id
                      AND p.event_datetime = e.event_datetime
        {where}
        GROUP BY 1
        HAVING sum(p.item_total_revenue) > 0
        ORDER BY revenue DESC LIMIT 5
        """,
        params,
    ).fetchdf()

    # 前十熱銷商品 (數量)
    items = con.execute(
        f"""
        SELECT COALESCE(NULLIF(p.item_name, ''), p.item_id) AS item,
               sum(p.item_quantity) AS qty
        FROM products p
        JOIN events e ON p.user_pseudo_id = e.user_pseudo_id
                      AND p.event_datetime = e.event_datetime
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
        cat = cat.iloc[::-1]
        fig_cat = px.bar(cat, x="revenue", y="category", orientation="h",
                         text="revenue", color="revenue",
                         color_continuous_scale=["#AED6F1", BLUE, NAVY])
        fig_cat.update_traces(texttemplate="$%{text:,.0f}", textposition="outside")
        fig_cat.update_coloraxes(showscale=False)
    fig_cat.update_layout(
        margin=dict(l=10, r=30, t=10, b=10),
        yaxis_title="", xaxis_title="營收 (USD)",
        plot_bgcolor="white", paper_bgcolor="white", font=dict(family="Segoe UI"),
    )

    # 熱銷商品橫條
    if items.empty:
        fig_items = go.Figure().add_annotation(text="此篩選條件下無商品資料", showarrow=False)
    else:
        items = items.iloc[::-1]
        fig_items = px.bar(items, x="qty", y="item", orientation="h",
                           text="qty", color="qty",
                           color_continuous_scale=["#A9DFBF", "#18BC9C", "#1A7A5E"])
        fig_items.update_traces(texttemplate="%{text:,}", textposition="outside")
        fig_items.update_coloraxes(showscale=False)
    fig_items.update_layout(
        margin=dict(l=10, r=30, t=10, b=10),
        yaxis_title="", xaxis_title="數量",
        plot_bgcolor="white", paper_bgcolor="white", font=dict(family="Segoe UI"),
    )

    return fig_cat, fig_items, 0


# ──────────────────────────────────────────────────────────────
# 6. Callback：事件分頁表
# ──────────────────────────────────────────────────────────────
@app.callback(
    Output("events-table", "data"),
    Output("events-table", "page_count"),
    Input("events-table",  "page_current"),
    Input("events-table",  "page_size"),
    Input("medium-filter", "value"),
    Input("keyword",       "value"),
    Input("date-range",    "start_date"),
    Input("date-range",    "end_date"),
)
def paginate_events(page_current, page_size, mediums, keyword, date_start, date_end):
    page_current = page_current or 0
    where, params = build_filter(mediums, keyword, date_start, date_end)
    con = db()
    total = con.execute(f"SELECT count(*) FROM events e {where}", params).fetchone()[0]
    page_count = max(1, -(-total // page_size))

    rows = con.execute(
        f"""
        SELECT CAST(e.event_date AS VARCHAR)                      AS event_date,
               strftime(e.event_datetime, '%H:%M:%S')             AS event_datetime,
               e.event_name,
               COALESCE(e.country, '-')                           AS country,
               COALESCE(e.device_type, '-')                       AS device_type,
               COALESCE(NULLIF(e.traffic_medium, ''), '(direct)') AS traffic_medium,
               COALESCE(NULLIF(e.campaign_name, ''), '-')         AS campaign_name,
               COALESCE(e.transaction_id, '-')                    AS transaction_id,
               ROUND(COALESCE(e.total_purchase_revenue, 0), 2)    AS total_purchase_revenue
        FROM events e {where}
        ORDER BY e.event_datetime DESC
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
    Input("keyword",        "value"),
    Input("date-range",     "start_date"),
    Input("date-range",     "end_date"),
)
def paginate_products(page_current, page_size, mediums, keyword, date_start, date_end):
    page_current = page_current or 0
    where, params = build_filter(mediums, keyword, date_start, date_end)
    con = db()

    total = con.execute(
        f"""
        SELECT count(*) FROM products p
        JOIN events e ON p.user_pseudo_id = e.user_pseudo_id
                      AND p.event_datetime = e.event_datetime
        {where}
        """,
        params,
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
        JOIN events e ON p.user_pseudo_id = e.user_pseudo_id
                      AND p.event_datetime = e.event_datetime
        {where}
        ORDER BY p.event_datetime DESC
        LIMIT {int(page_size)} OFFSET {int(page_current) * int(page_size)}
        """,
        params,
    ).fetchdf()
    return rows.to_dict("records"), page_count


# ── 重設按鈕 ──
@app.callback(
    Output("medium-filter", "value"),
    Output("keyword",       "value"),
    Output("date-range",    "start_date"),
    Output("date-range",    "end_date"),
    Input("reset-btn",      "n_clicks"),
    prevent_initial_call=True,
)
def reset(_):
    return None, "", MIN_DATE, MAX_DATE


if __name__ == "__main__":
    app.run(debug=False, port=8050)
