import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from bs4 import BeautifulSoup
import requests
import json
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from datetime import datetime, timedelta
from statistics import NormalDist
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

# 頁面基本設定
st.set_page_config(page_title="Stock analysis", layout="wide")

# 主題色：深藍 + 暗金
NAVY = "#0B1426"
NAVY_LIGHT = "#13213D"
GOLD = "#D4AF37"
GOLD_SOFT = "#B8962E"
TEXT = "#E6E9EF"
MUTED = "#8A96AD"

# 用 st.html 注入樣式 (新版 st.markdown 會把 <style> 當成文字顯示)
st.html(f"""<style>
    .stApp {{ background: linear-gradient(180deg, {NAVY} 0%, #0E1A30 100%); }}
    section[data-testid="stSidebar"] {{ background-color: {NAVY_LIGHT}; border-right: 1px solid rgba(212,175,55,0.25); }}
    h1, h2, h3 {{ color: {GOLD} !important; letter-spacing: 0.5px; }}

    /* 指標卡片：加大間距與字體 */
    div[data-testid="stMetric"] {{
        background: {NAVY_LIGHT};
        border: 1px solid rgba(212,175,55,0.30);
        border-radius: 10px;
        padding: 10px 14px;
        margin-bottom: 6px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.20);
    }}
    div[data-testid="stMetricLabel"] p {{ font-size: 0.8rem; color: {MUTED}; }}
    div[data-testid="stMetricValue"] {{ font-size: 1.25rem; font-weight: 600; color: {TEXT}; padding: 2px 0; }}
    div[data-testid="stMetricDelta"] {{ font-size: 0.78rem; }}

    /* 分頁樣式 */
    .stTabs [data-baseweb="tab-list"] {{ gap: 8px; }}
    .stTabs [data-baseweb="tab"] {{
        background: {NAVY_LIGHT}; border-radius: 10px 10px 0 0; padding: 10px 22px; color: {MUTED};
    }}
    .stTabs [aria-selected="true"] {{ color: {GOLD} !important; border-bottom: 2px solid {GOLD} !important; }}

    /* 按鈕：primary = 金底 (已選取)，secondary = 深藍底 (未選取) */
    .stButton > button[kind="primary"], button[data-testid="stBaseButton-primary"] {{
        background: {GOLD}; color: {NAVY}; border: 1px solid {GOLD}; font-weight: 700;
    }}
    .stButton > button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {{
        background: {GOLD_SOFT}; color: {NAVY}; border-color: {GOLD_SOFT};
    }}
    .stButton > button[kind="secondary"], button[data-testid="stBaseButton-secondary"] {{
        background: {NAVY}; color: {TEXT}; border: 1px solid rgba(212,175,55,0.30);
    }}
    .stButton > button[kind="secondary"]:hover, button[data-testid="stBaseButton-secondary"]:hover {{
        border-color: {GOLD}; color: {GOLD};
    }}

    /* 側邊欄股票按鈕：文字靠左；「−」移除鈕 hover 變紅 */
    [class*="st-key-pick_"] button {{
        justify-content: flex-start; text-align: left;
        min-height: 28px; height: 28px; padding: 0 10px; border-radius: 6px;
    }}
    [class*="st-key-pick_"] button p {{ font-size: 0.82rem; line-height: 1; }}
    [class*="st-key-del_"] button {{
        padding: 0; min-height: 28px; height: 28px; border-radius: 6px; font-size: 1rem; font-weight: 700;
    }}
    /* 側邊欄股票列之間的間距縮小 */
    section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {{ margin-bottom: -10px; }}
    section[data-testid="stSidebar"] .cat-header {{ font-size: 1.1rem; padding: 6px 10px; margin: 14px 0 6px 0; }}
    [class*="st-key-del_"] button:hover {{ background: #E5484D !important; color: white !important; border-color: #E5484D !important; }}

    /* 側邊欄產業大分類標題 */
    .cat-header {{
        font-size: 1.3rem; font-weight: 700; color: {TEXT};
        background: {NAVY}; border-left: 6px solid {GOLD}; border-radius: 8px;
        padding: 8px 12px; margin: 18px 0 4px 0;
        display: flex; align-items: center; justify-content: space-between;
    }}
    .cat-count {{ font-size: 0.8rem; font-weight: 500; color: {GOLD}; }}

    .sector-badge {{
        display: flex; align-items: center; gap: 10px;
        background: {NAVY}; border-radius: 10px; padding: 10px 12px; margin: 6px 0 4px 0;
    }}
    .sector-badge .swatch {{ width: 10px; align-self: stretch; border-radius: 4px; }}
    .sector-badge .icon {{ font-size: 1.5rem; }}
    .sector-badge .name {{ color: {TEXT}; font-weight: 600; }}
    .sector-badge .sub {{ color: {MUTED}; font-size: 0.8rem; }}
</style>""")

st.title("📈 Stock analysis")

# 股票池 (上市用 .TW、上櫃用 .TWO)
STOCK_POOL = {
    "半導體先進封裝": {"2330.TW": "台積電", "3711.TW": "日月光投控", "6223.TWO": "旺矽", "6515.TW": "穎崴"},
    "AI伺服器代工群": {"2317.TW": "鴻海", "3231.TW": "緯創", "2382.TW": "廣達"},
    "光電與重電綠能": {"1519.TW": "華城", "2409.TW": "友達", "3481.TW": "群創"},
    "大盤市值與高股息": {"0050.TW": "元大台灣50", "0056.TW": "元大高股息", "00878.TW": "國泰永續高股息"}
}
DEFAULT_CATEGORIES = list(STOCK_POOL)
DEFAULT_MEMBERS = {cat: list(pool) for cat, pool in STOCK_POOL.items()}

# 產業類別的 icon 與色塊 (自動分類的產業依序套用 GROUP_COLORS)
CATEGORY_STYLE = {
    "半導體先進封裝": ("🔬", "#4C8DFF"),
    "AI伺服器代工群": ("🖥️", "#9B6BFF"),
    "光電與重電綠能": ("⚡", "#2EC4A6"),
    "大盤市值與高股息": ("🏦", GOLD),
}
GROUP_COLORS = ["#FF8A4C", "#E5484D", "#30A46C", "#F5A524", "#00B8D9", "#D6409F", "#8A96AD"]


def category_style(cat):
    if cat in CATEGORY_STYLE:
        return CATEGORY_STYLE[cat]
    return INDUSTRY_ICONS.get(cat, "📁"), GROUP_COLORS[sum(map(ord, cat)) % len(GROUP_COLORS)]

# 自選股存檔 (重新啟動後仍保留)
WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"


def load_watchlist():
    """{"custom": {代號: 名稱}, "hidden": [被移除的預設股票]}；分類由 classify() 自動決定"""
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if "custom" not in data:  # 最舊格式：整個檔案就是 {代號: 名稱}
        data = {"custom": data}
    data.setdefault("hidden", [])
    data.pop("groups", None)  # 舊版自訂群組已取消
    data["custom"] = {code: (info["name"] if isinstance(info, dict) else info)
                      for code, info in data["custom"].items()}
    return data


def save_watchlist(watchlist):
    WATCHLIST_FILE.write_text(json.dumps(watchlist, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────── 模型與資料 (stock_model.py，與每日檢討共用) ───────────────
import importlib
import os
import stock_model as M

# Streamlit Cloud 更新程式時只會重跑 app.py，已載入的 stock_model 可能還是舊版 → 檔案有變就重新載入
_model_mtime = os.path.getmtime(M.__file__)
if getattr(M, "_loaded_mtime", _model_mtime) != _model_mtime:
    M = importlib.reload(M)
M._loaded_mtime = _model_mtime

# 替資料函式加上快取 (必須在 from stock_model import * 之前，才會拿到快取版本)
_CACHE_TTL = {
    "load_official_quotes": 600, "load_batch_history": 300, "load_max_history": 86400, "load_industry_map": 86400, "load_company_names": 86400, "load_news": 1800, "load_etf_holdings": 86400,
    "load_us_overnight": 900, "load_taifex_night": 900, "load_taifex_foreign_oi": 3600, "load_t86": 86400,
    "load_tpex_insti": 3600, "load_margin": 3600, "load_market_context": 1800, "load_seasonality": 86400,
    "load_long_history": 86400, "load_official_valuation": 3600, "load_yahoo_extras": 86400,
    "load_monthly_revenue": 21600, "load_profitability": 86400, "load_holders": 21600, "load_sbl": 3600,
    "load_put_call": 3600, "load_dividend_calendar": 21600, "load_us_earnings": 86400, "load_macro": 43200,
}
if not getattr(M, "_streamlit_cached", False):  # Streamlit 每次重跑都會執行這裡，只能包一次
    for _name, _ttl in _CACHE_TTL.items():
        if hasattr(M, _name):
            setattr(M, _name, st.cache_data(ttl=_ttl, show_spinner=False)(getattr(M, _name)))
    M.load_data = st.cache_data(ttl=300, max_entries=30, show_spinner=False)(M.load_data)
    M.load_pe_history = st.cache_data(ttl=86400, show_spinner="正在向證交所載入近一年本益比（約 20 秒）...")(M.load_pe_history)
    M._streamlit_cached = True

from stock_model import *  # noqa: E402,F401,F403
from stock_model import _to_float, _flatten  # noqa: E402  (底線開頭的名稱不會被 * 匯入)


def classify(code):
    """新增的股票自動分類：若某個預設類別多數成員與它同產業，就放進該類別；否則以官方產業別當分類"""
    industry = industry_of(code)
    for cat, members in DEFAULT_MEMBERS.items():
        same = sum(industry_of(c) == industry for c in members)
        if same * 2 > len(members):
            return cat
    return industry


if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist()

WATCH = st.session_state.watchlist
DEFAULT_STOCKS = {code: name for pool in STOCK_POOL.values() for code, name in pool.items()}
STOCK_POOL = {cat: {c: n for c, n in pool.items() if c not in WATCH["hidden"]} for cat, pool in STOCK_POOL.items()}
for code, name in list(WATCH["custom"].items()):  # 新增的股票依產業自動分類
    if not name or name == code or name == code.split(".")[0]:  # 手動加入時沒填名稱 → 自動查中文名並存檔
        name = lookup_stock_name(code) or code
        if name != code:
            WATCH["custom"][code] = name
            save_watchlist(WATCH)
    STOCK_POOL.setdefault(classify(code), {})[code] = name
ALL_STOCKS = {code: name for pool in STOCK_POOL.values() for code, name in pool.items()}
CODE_TO_CATEGORY = {code: cat for cat, pool in STOCK_POOL.items() for code in pool}


def add_to_watchlist():
    codes = [(c, MARKET[c][0]) for c in st.session_state.get("add_pick", [])]
    manual = st.session_state.get("add_manual", "").strip().upper()
    if manual:
        code = manual if "." in manual else manual + st.session_state.get("add_board", ".TW")
        codes.append((code, st.session_state.get("add_manual_name", "").strip() or lookup_stock_name(code) or code))
    for code, name in codes:
        if code in DEFAULT_STOCKS:  # 之前移除的預設股票 → 回到原本類別
            if code in WATCH["hidden"]:
                WATCH["hidden"].remove(code)
        else:
            WATCH["custom"][code] = name
        st.session_state[f"chk_{code}"] = True
    save_watchlist(WATCH)
    st.session_state.add_pick = []
    st.session_state.add_manual = ""
    st.session_state.add_manual_name = ""


def remove_codes(codes):
    for code in codes:
        WATCH["custom"].pop(code, None)
        if code in DEFAULT_STOCKS and code not in WATCH["hidden"]:
            WATCH["hidden"].append(code)
        st.session_state.pop(f"chk_{code}", None)
    save_watchlist(WATCH)


@st.dialog("確認移除股票")
def confirm_remove(code):
    st.markdown(f"確定要從「**{CODE_TO_CATEGORY[code]}**」移除 **{ALL_STOCKS[code]}（{code}）** 嗎？")
    if code in DEFAULT_STOCKS:
        st.caption("這是預設股票，之後可以在「新增股票」按「還原預設股票」找回。")
    c_yes, c_no = st.columns(2)
    if c_yes.button("確定移除", type="primary", width="stretch"):
        remove_codes([code])
        st.rerun()
    if c_no.button("取消", width="stretch"):
        st.rerun()


def toggle_pick(code):
    st.session_state[f"chk_{code}"] = not st.session_state.get(f"chk_{code}", False)


def restore_defaults():
    WATCH["hidden"] = []
    save_watchlist(WATCH)


def clear_checks():
    for code in ALL_STOCKS:
        st.session_state[f"chk_{code}"] = False


# AI 供應商與模型
AI_PROVIDERS = {
    "Claude": ["claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"],
    "OpenAI": ["gpt-4o-mini", "gpt-4o"],
}

# 側邊欄控制
PAGES = ["🏠 首頁", "🔎 個股分析"]
st.sidebar.radio("頁面", PAGES, key="page", horizontal=True, label_visibility="collapsed")
st.sidebar.header("🎯 標的選擇系統")
st.sidebar.caption("點一下股票即可選取（金色底 = 已選取），選取的股票才會抓資料與分析；按右邊「−」可移除。")

# 各類別股票清單：點股票切換選取，右邊「−」移除 (會先跳出確認視窗)
for cat, pool in STOCK_POOL.items():
    if not pool:
        continue
    icon, color = category_style(cat)
    n_checked = sum(st.session_state.get(f"chk_{c}", False) for c in pool)
    title = cat if cat.startswith(icon) else f"{icon} {cat}"
    count = f'<span class="cat-count">已選 {n_checked}</span>' if n_checked else ""
    st.sidebar.html(f'<div class="cat-header" style="border-left-color:{color};">{title}{count}</div>')
    for code, name in pool.items():
        picked = st.session_state.get(f"chk_{code}", False)
        c_pick, c_del = st.sidebar.columns([6, 1], gap="small", vertical_alignment="center")
        c_pick.button(f"{'✓ ' if picked else ''}{name}（{code.split('.')[0]}）", key=f"pick_{code}",
                      type="primary" if picked else "secondary", on_click=toggle_pick, args=(code,),
                      width="stretch")
        if c_del.button("−", key=f"del_{code}", help=f"移除 {name}", width="stretch"):
            confirm_remove(code)

checked_codes = [c for c in ALL_STOCKS if st.session_state.get(f"chk_{c}", False)]
stock_code = None
if checked_codes:
    st.sidebar.button(f"取消全部選取（目前 {len(checked_codes)} 檔）", on_click=clear_checks, width="stretch")
    st.sidebar.markdown("---")
    if st.session_state.get("main_stock") not in checked_codes:  # 主分析標的被取消選取時重設
        st.session_state.pop("main_stock", None)
    stock_code = st.sidebar.selectbox("🔎 主分析標的", checked_codes, format_func=lambda c: f"{ALL_STOCKS[c]} ({c})", key="main_stock")
    stock_name = ALL_STOCKS[stock_code]
    category = CODE_TO_CATEGORY[stock_code]
    cat_icon, cat_color = category_style(category)
    st.sidebar.html(f"""<div class="sector-badge">
    <div class="swatch" style="background:{cat_color};"></div>
    <div class="icon">{cat_icon}</div>
    <div><div class="name">{stock_name} <span class="sub">{stock_code}</span></div>
    <div class="sub" style="color:{cat_color};">{category}</div></div>
</div>""")

# 新增股票 (放在股票清單下方)
st.sidebar.markdown("---")
MARKET = load_market_list()
with st.sidebar.expander("➕ 新增股票", expanded=True):
    if MARKET:
        st.multiselect(
            "搜尋全市場 (輸入代號或名稱)",
            options=[c for c in MARKET if c not in ALL_STOCKS],
            format_func=lambda c: f"{c.split('.')[0]} {MARKET[c][0]} ({MARKET[c][1]})",
            key="add_pick",
        )
    else:
        st.caption("⚠️ 無法取得全市場清單，請手動輸入代號。")
    st.text_input("或手動輸入代號", key="add_manual", placeholder="例如：2454")
    st.text_input("股票名稱 (選填，留空會自動查詢)", key="add_manual_name", placeholder="例如：聯發科")
    st.radio("市場", [".TW", ".TWO"], key="add_board", horizontal=True,
             format_func=lambda s: "上市 (.TW)" if s == ".TW" else "上櫃 (.TWO)")
    st.caption("新增後會依證交所 / 櫃買中心的產業別自動歸類（例如聯發科 → 半導體類、ETF → 大盤市值與高股息）。")
    st.button("加入並選取", type="primary", on_click=add_to_watchlist, width="stretch")
    if WATCH["hidden"]:
        st.button(f"↩️ 還原被移除的預設股票（{len(WATCH['hidden'])} 檔）", on_click=restore_defaults, width="stretch")

# AI設定區
st.sidebar.markdown("---")
st.sidebar.header("🤖 AI 窗口設定")
ai_provider = st.sidebar.selectbox("選擇 AI 供應商", list(AI_PROVIDERS.keys()))
api_key = st.sidebar.text_input(f"輸入 {ai_provider} API Key", type="password", help=f"請輸入您的 {ai_provider} API 金鑰")
model_choice = st.sidebar.selectbox("選擇 AI 模型", AI_PROVIDERS[ai_provider])




def style_fig(fig, height):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=NAVY_LIGHT,
        margin=dict(l=20, r=20, t=30, b=20),
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="rgba(138,150,173,0.15)")
    fig.update_yaxes(gridcolor="rgba(138,150,173,0.15)")
    return fig


# ─────────────── 首頁：所有選股的走勢漲跌一覽 ───────────────
def go_analyze(table_key, codes):
    """首頁表格點選某一列 → 選取該股並切換到個股分析"""
    rows = st.session_state[table_key].selection.rows
    if rows:
        code = codes[rows[0]]
        st.session_state[f"chk_{code}"] = True
        st.session_state["main_stock"] = code
        st.session_state["page"] = PAGES[1]


def latest_model_calls():
    """每日檢討最新一次的預測 (代號 → 隔天 / 1 週上漲機率)"""
    path = DATA_DIR / "predictions.csv"
    if not path.exists():
        return {}
    p = pd.read_csv(path, dtype={"code": str})
    p = p[p["base_date"] == p["base_date"].max()]
    return {r["code"]: (r["final_p"], r.get("week_p", np.nan)) for _, r in p.iterrows()}


def render_risk_section(hist, table):
    """題材集中度、行情轉變警示、歷史修正壓力測試"""
    series = {c: hist[c] for c in table["code"] if c in hist}
    regime = regime_status(series)
    if regime and regime["alerts"]:
        st.error("🚨 **行情轉變警示**\n\n" + "\n".join(f"- {a}" for a in regime["alerts"]))

    st.subheader("🧭 題材集中度與修正壓力測試")
    conc = concentration(series, {c: industry_of(c) for c in series})
    k1, k2, k3, k4 = st.columns(4, gap="small")
    k1.metric("AI / 半導體 / 電子占比", f"{conc['tech_share'] * 100:.0f}%", delta=f"{conc['n']} 檔中", delta_color="off")
    k2.metric("選股平均連動（相關係數）", f"{conc['avg_corr']:.2f}",
              delta="高度同步，分散效果有限" if conc["avg_corr"] >= 0.4 else "分散程度尚可",
              delta_color="inverse" if conc["avg_corr"] >= 0.4 else "off")
    k3.metric("與台積電的平均連動", f"{conc['corr_tsmc']:.2f}" if not np.isnan(conc["corr_tsmc"]) else "—")
    if regime:
        k4.metric("近 20 日連動 / 波動", f"{regime['corr_now']:.2f} / {regime['vol_now']:.0f}%",
                  delta=f"平常 {regime['corr_median']:.2f} / {regime['vol_median']:.0f}%", delta_color="off")

    stress = stress_test(series)
    if not stress.empty:
        stress.index = [ALL_STOCKS[c] for c in stress.index]
        avg = stress.mean()
        stress.loc["📊 清單平均"] = avg
        color = lambda v: f"color: {'#30A46C' if v < -20 else MUTED}" if pd.notna(v) else ""
        st.markdown("**📉 如果重演過去的修正：每檔在各次修正期間的最大跌幅**")
        st.dataframe(stress.style.map(color).format("{:.1f}%", na_rep="上市前"), width="stretch")
        worst = avg.idxmin()
        st.caption(f"清單平均在「{worst}」期間最大跌了 {avg.min():.1f}%。題材集中時，一旦修正往往一起下跌；"
                   "這是歷史數據的壓力測試，不代表未來會重演，也不是投資建議。")


def render_buy_ranking(table, calls):
    """今日買進參考排名：依每日檢討最新預測排序，並附 20 年回測的歷史勝率 (區分跳空與實際可交易)"""
    st.subheader("🏆 今日買進參考排名（數據分析）")
    ranked = []
    for _, r in table.iterrows():
        day_p, week_p = calls.get(r["code"], (None, None))
        if day_p is None or pd.isna(day_p):
            continue
        week_p = 50.0 if week_p is None or pd.isna(week_p) else float(week_p)
        signal = "✅ 偏多" if day_p >= 55 and week_p >= 52 else "⚠️ 偏空" if day_p <= 45 and week_p <= 48 else "➖ 觀望"
        ranked.append({"code": r["code"], "股票": r["股票"], "參考訊號": signal, "隔天上漲機率": float(day_p), "1 週上漲機率": week_p,
                       "_order": {"✅ 偏多": 0, "➖ 觀望": 1, "⚠️ 偏空": 2}[signal]})
    if not ranked:
        st.caption("尚無模型預測。每日檢討會在交易日早上 07:00（開盤前）自動產生，之後這裡就會顯示排名。")
        return
    rank = pd.DataFrame(ranked).sort_values(["_order", "隔天上漲機率", "1 週上漲機率"], ascending=[True, False, False])
    win_path = DATA_DIR / "backtest" / "win_rates.json"
    wins = json.loads(win_path.read_text(encoding="utf-8")) if win_path.exists() else {}
    buckets = wins.get("buckets", {})

    def bucket_of(p):
        for label, lo, hi in [("≥ 65%", 65, 101), ("60~65%", 60, 65), ("55~60%", 55, 60), ("45~55%", 45, 55), ("< 45%", 0, 45)]:
            if lo <= p < hi:
                return buckets.get(label, {})
        return {}

    pct = lambda v: f"{v * 100:.1f}%" if v is not None else "—"
    rows = []
    for i, r in enumerate(rank.to_dict("records"), 1):
        b = bucket_of(r["隔天上漲機率"])
        rows.append({"排名": i, "股票": r["股票"], "參考訊號": r["參考訊號"],
                     "隔天上漲機率": f"{r['隔天上漲機率']:.0f}%", "1 週上漲機率": f"{r['1 週上漲機率']:.0f}%",
                     "歷史勝率：收盤→隔天收盤": pct(b.get("win_gap")),
                     "歷史勝率：開盤買、當天賣": pct(b.get("win_oc1")),
                     "歷史勝率：開盤買、抱 2 天": pct(b.get("win_oc2")),
                     "抱 2 天平均報酬（未扣成本）": f"{b['avg_oc2']:+.2f}%" if b.get("avg_oc2") is not None else "—",
                     "歷史樣本": b.get("n", 0)})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    sig = wins.get("signals", {})
    if sig:
        bull, allx = sig.get("偏多", {}), sig.get("全部", {})
        st.warning(
            "**如何解讀勝率（20 年回測，13 檔、約 1 萬個樣本）**\n\n"
            f"- **收盤→隔天收盤**：模型真正預測到的部分。「偏多」時 {pct(bull.get('win_gap'))}，全部平均 {pct(allx.get('win_gap'))}。"
            "但模型要等美股收盤後（早上）才算得出來，**你買不到前一天的收盤價**。\n"
            f"- **開盤買進**：你實際能做到的。「偏多」時當天賣勝率 {pct(bull.get('win_oc1'))}、抱 2 天 {pct(bull.get('win_oc2'))}，"
            f"全部平均分別是 {pct(allx.get('win_oc1'))}、{pct(allx.get('win_oc2'))}。偏多時略好，但幾乎等於擲硬幣，"
            f"而且平均報酬（{bull.get('avg_oc2', 0):+.2f}%）遠低於一趟交易成本約 {wins.get('cost_round_trip_pct', 0.47)}%。\n\n"
            "排名是依模型數據排序的**分析參考**，不是投資建議；統計上照著短線買賣並沒有優勢。", icon="⚠️")


def render_home():
    st.header("🏠 我的選股一覽")
    codes = tuple(ALL_STOCKS)
    if not codes:
        st.info("清單是空的，請在左側「➕ 新增股票」加入股票。")
        return
    with st.spinner("載入所有選股的最新行情 ..."):
        hist = load_batch_history(codes, period="10y")
    calls = latest_model_calls()
    def buy_reference(day_p, week_p):
        """買進參考：隔天與 1 週模型同向才表態，否則觀望 (數據分析參考，非投資建議)"""
        if day_p is None or pd.isna(day_p):
            return "— 尚無資料"
        week_p = 50.0 if week_p is None or pd.isna(week_p) else week_p
        detail = f"（隔天 {day_p:.0f}%／1 週 {week_p:.0f}%）"
        if day_p >= 55 and week_p >= 52:
            return "✅ 偏多" + detail
        if day_p <= 45 and week_p <= 48:
            return "⚠️ 偏空" + detail
        return "➖ 觀望" + detail
    def ret_since(s, offset=None, month_start=False):
        """以日曆日計算報酬：當月 = 相對上個月最後一個交易日；其他 = 相對 N 個月前最近的交易日"""
        cutoff = s.index[-1].replace(day=1) if month_start else s.index[-1] - offset
        past = s[s.index < cutoff] if month_start else s[s.index <= cutoff]
        if past.empty:
            return np.nan
        return float((s.iloc[-1] / past.iloc[-1] - 1) * 100)
    rows = []
    for code in codes:
        s = hist.get(code)
        if s is None:
            continue
        call = calls.get(code, (None, None))
        rows.append({"code": code, "分類": CODE_TO_CATEGORY[code], "股票": f"{ALL_STOCKS[code]}（{code.split('.')[0]}）",
                     "收盤": float(s.iloc[-1]), "漲跌": float(s.iloc[-1] - s.iloc[-2]),
                     "當日 %": float((s.iloc[-1] / s.iloc[-2] - 1) * 100), "當月 %": ret_since(s, month_start=True),
                     "3 個月 %": ret_since(s, pd.DateOffset(months=3)), "半年 %": ret_since(s, pd.DateOffset(months=6)),
                     "1 年 %": ret_since(s, pd.DateOffset(years=1)), "5 年 %": ret_since(s, pd.DateOffset(years=5)),
                     "近 3 月走勢": [float(v) for v in s.iloc[-63:]],
                     "買進參考（數據分析）": buy_reference(*call), "資料日": s.index[-1].strftime("%m/%d")})
    if not rows:
        st.warning("暫時抓不到行情資料，請稍後重新整理。")
        return
    table = pd.DataFrame(rows)

    up, down = int((table["當日 %"] > 0).sum()), int((table["當日 %"] < 0).sum())
    best, worst = table.loc[table["當日 %"].idxmax()], table.loc[table["當日 %"].idxmin()]
    m1, m2, m3, m4 = st.columns(4, gap="small")
    m1.metric("上漲 / 下跌 / 平盤", f"{up} / {down} / {len(table) - up - down}", delta=f"共 {len(table)} 檔", delta_color="off")
    m2.metric("平均漲跌", f"{table['當日 %'].mean():+.2f}%", delta=f"當月平均 {table['當月 %'].mean():+.2f}%")
    m3.metric("今日最強", best["股票"], delta=f"{best['當日 %']:+.2f}%")
    m4.metric("今日最弱", worst["股票"], delta=f"{worst['當日 %']:+.2f}%")

    render_risk_section(hist, table)
    render_buy_ranking(table, calls)

    period = st.radio("📊 漲跌排行期間", ["當日 %", "當月 %", "3 個月 %", "半年 %", "1 年 %", "5 年 %"],
                      horizontal=True, key="home_period", format_func=lambda c: c.replace(" %", ""))
    bar = table.dropna(subset=[period]).sort_values(period)
    fig = go.Figure(go.Bar(x=bar[period], y=bar["股票"], orientation="h",
                           marker_color=["#E5484D" if v > 0 else "#30A46C" if v < 0 else MUTED for v in bar[period]],
                           text=[f"{v:+.2f}%" for v in bar[period]], textposition="outside"))
    style_fig(fig, max(260, 28 * len(bar) + 60))
    fig.update_layout(showlegend=False, hovermode="closest", margin=dict(l=10, r=40, t=20, b=20))
    st.plotly_chart(fig, width="stretch")

    st.caption("紅 = 上漲、綠 = 下跌（台股慣例）。👉 點選任一列可直接進入該股的個股分析。")
    st.info("**「買進參考（數據分析）」怎麼判斷**：取每日檢討（開盤前 07:00）最新一次模型預測，"
            "隔天上漲機率 ≥55% 且 1 週 ≥52% → ✅ 偏多；隔天 ≤45% 且 1 週 ≤48% → ⚠️ 偏空；其餘 ➖ 觀望。\n\n"
            "**請注意 20 年回測的結果**：這個訊號主要反映「開盤會跳高還是跳低」。若在隔天開盤後才買進，"
            "偏多與偏空之後的平均報酬幾乎相同，扣除交易成本（約 0.47%）後沒有統計優勢。"
            "僅供數據分析參考，不是投資建議，買賣請自行判斷風險。", icon="ℹ️")
    pct_cols = ["當日 %", "當月 %", "3 個月 %", "半年 %", "1 年 %", "5 年 %"]
    color = lambda v: f"color: {'#E5484D' if v > 0 else '#30A46C' if v < 0 else MUTED}" if pd.notna(v) else ""
    for i, (cat, g) in enumerate(table.groupby("分類", sort=False)):
        icon, cat_color = category_style(cat)
        st.html(f'<div class="cat-header" style="border-left-color:{cat_color};">{cat if cat.startswith(icon) else f"{icon} {cat}"}'
                f'<span class="cat-count">平均 {g["當日 %"].mean():+.2f}%</span></div>')
        show = g.drop(columns=["code", "分類"]).reset_index(drop=True)
        key = f"home_table_{i}"
        st.dataframe(
            show.style.map(color, subset=pct_cols + ["漲跌"])
                .format({"收盤": "{:.2f}", "漲跌": "{:+.2f}", **{c: "{:+.2f}%" for c in pct_cols}}, na_rep="—"),
            column_config={"近 3 月走勢": st.column_config.LineChartColumn("近 3 月走勢", width="medium")},
            width="stretch", hide_index=True, key=key, on_select=lambda k=key, c=list(g["code"]): go_analyze(k, c),
            selection_mode="single-row")


if st.session_state.get("page", PAGES[0]) == PAGES[0]:
    render_home()
    st.stop()

# 待機模式：沒有選取任何股票就不抓資料
if not checked_codes:
    st.info("💤 目前為待機模式：請在左側點選想分析的股票（選取 2 檔以上可使用多股比較）。")
    st.caption(f"備用清單共 {len(ALL_STOCKS)} 檔，未選取的股票不會下載資料。")
    st.stop()

try:
    df = get_price_data(stock_code)
    if df.empty:
        st.warning(f"{stock_name} ({stock_code}) 查無資料，請確認代號與上市/上櫃是否正確。")
        st.stop()

    df = add_indicators(df)
    last = df.iloc[-1]

    current_price = float(last['Close'])
    price_change = float(df['Close'].iloc[-1] - df['Close'].iloc[-2])
    price_pct = float(price_change / df['Close'].iloc[-2] * 100)
    bias_60 = float((last['Close'] - last['60MA']) / last['60MA'] * 100)
    current_k, current_d = float(last['K']), float(last['D'])
    current_rsi = float(last['RSI'])
    current_dif, current_macd = float(last['DIF']), float(last['MACD'])

    # KD 狀態
    kd_cross = cross_signal(df['K'], df['D'])
    kd_status = {"golden": "🔥 KD 黃金交叉 (多頭)", "death": "⚠️ KD 死亡交叉 (空頭)"}.get(kd_cross, "➡️ KD 區間震盪")

    # RSI 狀態
    if current_rsi > 70:
        rsi_text, rsi_color = "⚠️ 超買區 (>70)", "inverse"
    elif current_rsi < 30:
        rsi_text, rsi_color = "🚨 超賣區 (<30)", "normal"
    else:
        rsi_text, rsi_color = "➡️ 中性區間", "off"

    # MACD 狀態
    macd_cross = cross_signal(df['DIF'], df['MACD'])
    if macd_cross == "golden":
        macd_text, macd_color = "🔥 MACD 黃金交叉", "normal"
    elif macd_cross == "death":
        macd_text, macd_color = "⚠️ MACD 死亡交叉", "inverse"
    elif current_dif > current_macd:
        macd_text, macd_color = "➡️ DIF 在訊號線上 (偏多)", "off"
    else:
        macd_text, macd_color = "➡️ DIF 在訊號線下 (偏空)", "off"

    # 布林通道狀態
    if current_price >= last['BB_UP']:
        bb_text = "⚠️ 觸及布林上軌"
    elif current_price <= last['BB_LOW']:
        bb_text = "🚨 觸及布林下軌"
    else:
        bb_text = "➡️ 通道內運行"

    # 量能與位階
    ma20 = float(df['Close'].rolling(20).mean().iloc[-1])
    vol_ratio = float(last['Volume'] / df['Volume'].iloc[-21:-1].mean()) if 'Volume' in df.columns else np.nan
    high_52w, low_52w = float(df['High'].max()), float(df['Low'].min())
    pos_52w = (current_price - low_52w) / (high_52w - low_52w) * 100 if high_52w > low_52w else np.nan
    ret_1m = float((df['Close'].iloc[-1] / df['Close'].iloc[-22] - 1) * 100) if len(df) > 22 else np.nan

    # 綜合評估用的外部資料 (皆有快取，只針對主分析標的抓取)
    stock_news = load_news(f"{stock_name} {stock_code.split('.')[0]}")
    world_news = load_news("美股 OR 聯準會 OR 關稅 OR 地緣政治 OR 費半 OR 台股大盤", limit=10)
    # 主動搜尋利空與產業展望，避免只看到正面消息
    bad_news = load_news(f"{stock_name} (利空 OR 下修 OR 衰退 OR 虧損 OR 砍單 OR 調降 OR 賣超 OR 裁員 OR 競爭 OR 訴訟 OR 跌停)", days=30)
    outlook_news = load_news(f"{stock_name} (展望 OR 前景 OR 法說 OR 產業趨勢 OR 營收)", days=30)
    # 成長題材：訂單能見度、AI / 機器人等長期趨勢、地緣政治與供應鏈
    theme_news = load_news(f"{stock_name} (訂單 OR 產能 OR 擴產 OR 資本支出 OR AI OR 機器人 OR 地緣政治 OR 供應鏈)", days=30)
    market_ctx = load_market_context()
    seasonality = load_seasonality(stock_code)
    today = datetime.now(TAIPEI)

    # 估值：官方本益比 / 殖利率 / 股價淨值比 + 同類股比較 + 近一年本益比區間
    official_vals = load_official_valuation()
    own_val = official_vals.get(stock_code, {})
    current_pe = own_val.get("本益比", np.nan)
    peer_pes = [official_vals.get(c, {}).get("本益比", np.nan) for c in STOCK_POOL.get(category, {}) if c != stock_code]
    peer_pes = [p for p in peer_pes if not np.isnan(p)]
    peer_pe_median = float(np.median(peer_pes)) if len(peer_pes) >= 2 else np.nan

    pe_hist = pd.Series(dtype=float)
    pe_hist_key = f"pe_hist_{stock_code}"
    if st.session_state.get(pe_hist_key) and stock_code.endswith(".TW") and not np.isnan(current_pe):
        pe_hist = load_pe_history(stock_code.split('.')[0])
    pe_pct = float((pe_hist < current_pe).mean() * 100) if len(pe_hist) > 20 else np.nan

    valuation = {k: round(v, 2) for k, v in own_val.items() if not np.isnan(v)}
    if not np.isnan(peer_pe_median):
        valuation["同類股本益比中位數"] = round(peer_pe_median, 2)
    if not np.isnan(pe_pct):
        valuation["近一年本益比區間"] = f"{pe_hist.min():.1f} ~ {pe_hist.max():.1f}（平均 {pe_hist.mean():.1f}，目前位於 {pe_pct:.0f} 百分位）"
    valuation.update(load_yahoo_extras(stock_code))
    if not own_val:
        valuation["備註"] = "ETF 或無官方本益比資料"
    elif np.isnan(current_pe):
        valuation["備註"] = "近四季虧損，無本益比"
    this_m, next_m = today.month, today.month % 12 + 1

    # 未來走勢：歷史報酬分布、技術面支撐壓力、本益比推估合理價
    long_adj = load_long_history(stock_code)

    # ETF 成分股分析：前 10 大成分股的表現、風險燈號與持股增減
    is_etf = stock_code.split('.')[0].startswith("00")
    etf_holdings, etf_date, etf_top, etf_summary, etf_errors = pd.DataFrame(), "", pd.DataFrame(), "", []
    if is_etf:
        etf_holdings, etf_date, etf_top, etf_errors = analyze_etf_constituents(stock_code)
    if not etf_top.empty:
        weights = etf_top["權重 (%)"].fillna(0)
        contrib = (weights * etf_top["近一月 (%)"].fillna(0) / 100).sum()
        risky_weight = weights[etf_top["燈號"].str.contains("警戒|高風險")].sum()
        def big_change(s):
            v = _to_float(str(s).replace("%", ""))
            if np.isnan(v):  # 非數字 (例如新納入) 也視為重大變化
                return str(s).strip() not in ("", "-", "--")
            return abs(v) >= 5
        big_changes = etf_holdings[etf_holdings["持股增減"].map(big_change)]
        etf_summary = (
            f"持股資料日期 {etf_date}，共 {len(etf_holdings)} 檔成分股；前十大權重合計 {weights.sum():.1f}%，"
            f"最大成分股 {etf_top.iloc[0]['成分股']} 占 {etf_top.iloc[0]['權重 (%)']:.1f}%（集中度風險）。\n"
            f"前十大成分股近一月加權貢獻約 {contrib:+.2f} 個百分點；其中亮「警戒 / 高風險」燈號的權重合計 {risky_weight:.1f}%。\n"
            "前十大成分股：\n" + "\n".join(
                f"- {r['成分股']} 權重 {r['權重 (%)']:.2f}%，近一月 {r['近一月 (%)']:+.1f}%，{r['燈號']}，持股增減 {r['持股增減']}，警訊：{r['主要警訊'] or '無'}"
                for _, r in etf_top.iterrows()) +
            "\n持股增減幅度較大（±5% 以上）的成分股：" +
            ("、".join(f"{r['成分股']} {r['持股增減']}" for _, r in big_changes.head(10).iterrows()) or "無")
        )
    etf_news = load_news(f"{stock_name} 成分股 (調整 OR 納入 OR 剔除 OR 換股)", limit=6, days=90) if is_etf else []

    # 風險評估 (主分析標的含估值面；其他選取股票只看價量)
    pe_vs_peer = (current_pe / peer_pe_median - 1) * 100 if not np.isnan(peer_pe_median) and not np.isnan(current_pe) else np.nan
    risk = assess_risk(df, long_adj, pe_pct, pe_vs_peer)
    bias_text = risk["bias_text"]
    bias_color = "inverse" if risk["items"] and any(t in ("過熱", "弱勢") and "乖離" in s for _, t, s in risk["items"]) else "normal"
    risk_board = []
    for code in checked_codes:
        try:
            r = risk if code == stock_code else assess_risk(add_indicators(get_price_data(code)), load_long_history(code))
        except Exception:
            continue
        risk_board.append({
            "股票": f"{ALL_STOCKS[code]} ({code})",
            "燈號": f"{r['emoji']} {r['level']}",
            "風險分數": r["score"],
            "季線乖離": f"{r['bias']:+.1f}%" + (f"（{r['bias_pct']:.0f} 百分位）" if not np.isnan(r['bias_pct']) else ""),
            "主要警訊": "、".join(s for _, _, s in r["items"][:3]) or "無明顯警訊",
        })
    risk_board = pd.DataFrame(risk_board).sort_values("風險分數", ascending=False) if risk_board else pd.DataFrame()
    projection = compute_projection(long_adj, current_price) if len(long_adj) > 120 else pd.DataFrame()

    # 隔天多因子訊號：統計機率 + 美股 / ADR / 夜盤 / 法人 / 融資 / 夜間新聞
    stat_p_1d = float(projection.loc[projection["期間"] == "隔天", "上漲機率 (%)"].iloc[0]) \
        if not projection.empty and (projection["期間"] == "隔天").any() else 50.0
    try:
        nextday_table, nextday_p, night_news, _ = build_nextday_signals(stock_code, stock_name, df, stat_p_1d)
    except Exception:
        nextday_table, nextday_p, night_news = pd.DataFrame(), stat_p_1d, []

    # 多期間量化計分卡 (隔天 / 1 週 / 1 個月 / 1 年)：統計 + 技術 + 籌碼 + 基本面 + 估值 + 總經理論
    scorecards, model_inputs = {}, {}
    if not projection.empty:
        try:
            nextday_points = float(nextday_table["加減分"].sum()) if not nextday_table.empty else 0.0
            model_inputs = collect_model_inputs(stock_code, stock_name, df, long_adj, projection, nextday_p, nextday_points,
                                                pe_pct=pe_pct, pe_hist=pe_hist if len(pe_hist) else None)
            scorecards = build_horizon_scorecards(model_inputs)
        except Exception as e:
            st.warning(f"量化計分卡計算失敗：{e}")
    history_years = len(long_adj) / 252

    levels = {
        "20MA": ma20, "60MA": float(last['60MA']),
        "120MA": float(df['Close'].rolling(120).mean().iloc[-1]),
        "布林上軌": float(last['BB_UP']), "布林下軌": float(last['BB_LOW']),
        "20 日高點": float(df['High'].iloc[-20:].max()), "20 日低點": float(df['Low'].iloc[-20:].min()),
        "60 日高點": float(df['High'].iloc[-60:].max()), "60 日低點": float(df['Low'].iloc[-60:].min()),
        "52 週高點": high_52w, "52 週低點": low_52w,
    }
    levels = {k: v for k, v in levels.items() if not np.isnan(v)}
    supports = sorted([(v, k) for k, v in levels.items() if v < current_price], reverse=True)[:4]
    resistances = sorted([(v, k) for k, v in levels.items() if v > current_price])[:4]

    # 本益比推估合理價：除了近四季 EPS，也用「未來一年 EPS」(成長股若只用過去 EPS 會嚴重低估)
    pe_targets = {}
    eps_notes = []
    if not np.isnan(current_pe):
        eps_ttm = current_price / current_pe
        eps_bases = [("近四季 EPS", eps_ttm)]
        forward_eps = valuation.get("預估 EPS（分析師共識）")
        eps_growth = np.nan
        if len(pe_hist) > 150:  # 用每日本益比反推 EPS，估算近一年 EPS 成長率
            eps_series = (df['Close'].reindex(pe_hist.index) / pe_hist).dropna()
            if len(eps_series) > 100 and eps_series.iloc[0] > 0:
                eps_growth = float(eps_series.iloc[-1] / eps_series.iloc[0] - 1)
                eps_notes.append(f"近一年 EPS 成長 {eps_growth * 100:+.0f}%（由官方本益比反推）")
        if forward_eps:
            eps_bases.append(("預估 EPS（分析師共識）", forward_eps))
            eps_notes.append(f"分析師預估未來 EPS {forward_eps:.2f}，較近四季 {eps_ttm:.2f} {'成長' if forward_eps >= eps_ttm else '衰退'} {abs(forward_eps / eps_ttm - 1) * 100:.0f}%")
        elif not np.isnan(eps_growth):
            g = float(np.clip(eps_growth, -0.5, 0.6))
            eps_bases.append(("未來一年 EPS（延續近一年成長）", eps_ttm * (1 + g)))
        pe_levels = []
        if not np.isnan(pe_pct):
            pe_levels += [(f"歷史本益比 {int(q * 100)} 百分位" if q != 0.5 else "歷史本益比中位數", float(pe_hist.quantile(q)))
                          for q in (0.25, 0.5, 0.75)]
        if not np.isnan(peer_pe_median):
            pe_levels.append(("同類股本益比中位數", peer_pe_median))
        if not pe_levels:
            pe_levels.append(("目前本益比", current_pe))
        for eps_label, eps in eps_bases:
            for pe_label, pe in pe_levels:
                if eps_label == "近四季 EPS" and pe_label == "目前本益比":
                    continue  # 等於目前股價，沒有意義
                pe_targets[f"{eps_label} × {pe_label}"] = (pe, eps * pe)

    def projection_text():
        if projection.empty:
            return "（歷史資料不足）"
        return "\n".join(
            f"- {r['期間']}：10%={r['悲觀 (10%)']:.2f}、25%={r['保守 (25%)']:.2f}、中位數={r['中位數']:.2f}、"
            f"75%={r['樂觀 (75%)']:.2f}、90%={r['極樂觀 (90%)']:.2f}，上漲機率 {r['上漲機率 (%)']:.0f}%（已含基準年化成長 {r['基準年化成長 (%)']:.1f}%）；"
            f"若延續過去趨勢中位數 {r['若延續過去趨勢']:.2f}（過去平均漲幅 {r['過去平均漲幅 (%)']:+.1f}%，多頭期間不代表未來）；"
            f"相似情境樣本 {r['情境樣本']}；近 60 日波動度 ±1σ 區間 {r['目前波動 ±1σ']}"
            for _, r in projection.iterrows())

    def show_news(items):
        if not items:
            st.caption("暫時抓不到新聞。")
        for n in items:
            title = n['title'].replace("[", "［").replace("]", "］")
            st.markdown(f"- [{title}]({n['link']})  \n  <small style='color:{MUTED}'>{n['source']} · {n['date']}</small>",
                        unsafe_allow_html=True)

    def news_text(items):
        return "\n".join(f"- ({n['date']}) {n['title']}（{n['source']}）" for n in items) or "（無資料）"

    def season_text():
        if seasonality.empty:
            return "（無資料）"
        lines = [f"- {r['月份']}：平均 {r['平均報酬 (%)']:+.2f}%，中位數 {r['中位數 (%)']:+.2f}%，上漲機率 {r['上漲機率 (%)']:.0f}%（{r['樣本年數']} 年）"
                 for _, r in seasonality.iterrows()]
        return "\n".join(lines)

    # 基本面與籌碼：月營收、毛利率趨勢、集保大戶、借券
    stock_no = stock_code.split('.')[0]
    revenue = load_monthly_revenue().get(stock_code)
    profit = load_profitability().get(stock_code)
    profit_hist = load_profitability_history(stock_code)
    holders_now = load_holders().get(stock_no)
    holders_hist = load_holders_history(stock_no)
    fundamentals = []
    if revenue:
        fundamentals.append(f"月營收 {revenue['month']}：年增 {revenue['yoy']:+.1f}%、月增 {revenue['mom']:+.1f}%、"
                            f"今年累計年增 {revenue['cum_yoy']:+.1f}%" + (f"（公司說明：{revenue['note']}）" if revenue['note'] else ""))
    if profit:
        trend = ""
        if len(profit_hist) >= 2:
            prev_q = profit_hist.iloc[-2]
            trend = f"，較上一季 {prev_q['quarter']} 毛利率 {profit['gross'] - prev_q['gross']:+.2f} 個百分點"
        fundamentals.append(f"{profit['quarter']} 毛利率 {profit['gross']:.2f}%、營業利益率 {profit['operating']:.2f}%、"
                            f"稅後純益率 {profit['net']:.2f}%{trend}")
    if holders_now:
        prev_h = holders_hist[holders_hist["date"].astype(str) < holders_now["date"]] if not holders_hist.empty else pd.DataFrame()
        chg = f"（週變化 {holders_now['big1000'] - float(prev_h.iloc[-1]['big1000']):+.2f} 個百分點）" if not prev_h.empty else ""
        fundamentals.append(f"集保 {holders_now['date']}：千張大戶 {holders_now['big1000']:.2f}%{chg}、400 張以上 {holders_now['big400']:.2f}%、"
                            f"50 張以下散戶 {holders_now['retail']:.2f}%，股東 {holders_now['people']:,.0f} 人")
    upcoming = upcoming_events(stock_code, today.date())

    model_block = ""
    for h, c in scorecards.items():
        top = sorted(c["table"].dropna(subset=["加減分"]).to_dict("records"), key=lambda r: -abs(r["加減分"]))[:5]
        top_text = "、".join("{} {:+.1f}".format(r["因子"], r["加減分"]) for r in top)
        model_block += (f"- {h}：{c['direction']}，上漲機率 {c['prob']:.0f}%（基礎 {c['base']:.0f}% ＋ 因子 {c['total']:+.1f}），"
                        f"預估價 {c['price']:.2f}（±1σ {c['low']:.2f} ~ {c['high']:.2f}），信心 {c['confidence']}；主要因子：{top_text}\n")

    etf_block = ""
    if is_etf:
        etf_block = (f"【ETF 成分股分析】\n{etf_summary or '（抓不到持股明細）'}\n"
                     f"近 90 天成分股調整新聞：\n{news_text(etf_news)}\n")

    data_snapshot = f"""【標的】{stock_name}（{stock_code}），產業分類：{category}，資料時間：{today:%Y-%m-%d %H:%M}（台北）

【價格與技術面】
- 收盤價 {current_price:.2f}，日漲跌 {price_change:+.2f}（{price_pct:+.2f}%），近一月漲跌 {ret_1m:+.2f}%
- 20MA {ma20:.2f}，60MA {float(last['60MA']):.2f}，季線乖離 {bias_60:+.2f}%（{bias_text}）
- KD：K {current_k:.1f} / D {current_d:.1f}（{kd_status}）
- RSI(14)：{current_rsi:.1f}（{rsi_text}）
- MACD：DIF {current_dif:.2f} / 訊號線 {current_macd:.2f} / OSC {float(last['OSC']):.2f}（{macd_text}）
- 布林通道：下軌 {float(last['BB_LOW']):.2f} / 中軌 {float(last['BB_MID']):.2f} / 上軌 {float(last['BB_UP']):.2f}（{bb_text}）
- 成交量為近 20 日均量的 {vol_ratio:.2f} 倍
- 52 週區間 {low_52w:.2f} ~ {high_52w:.2f}，目前位於區間 {pos_52w:.0f}% 位置
- 風險燈號：{risk['emoji']} {risk['level']}（分數 {risk['score']}），警訊：{"、".join(s for _, _, s in risk['items']) or "無"}

【估值】{"、".join(f"{k} {v}" for k, v in valuation.items()) or "（無資料）"}

【基本面與籌碼】
{chr(10).join("- " + f for f in fundamentals) or "（無資料）"}

【未來兩週重要事件】
{chr(10).join(f"- {d_:%Y-%m-%d} {t}" for d_, t in upcoming) or "（無）"}
【本益比推估合理價】{"、".join(f"{k}（{pe:.1f} 倍）→ {p:.2f}" for k, (pe, p) in pe_targets.items()) or "（無資料）"}
【EPS 成長】{"；".join(eps_notes) or "（無資料）"}（成長股應以未來 EPS 評估 1 年合理價，只用近四季 EPS 會低估）

【支撐與壓力】
- 支撐：{"、".join(f"{k} {v:.2f}" for v, k in supports) or "（無）"}
- 壓力：{"、".join(f"{k} {v:.2f}" for v, k in resistances) or "（創一年新高，無上方壓力）"}

【歷史報酬分布推估的未來價格（近 {history_years:.1f} 年日資料，以目前股價 {current_price:.2f} 為基準）】
{projection_text()}

【國際與大盤指標】
{market_ctx.to_string(index=False) if not market_ctx.empty else "（無資料）"}

【月份效應（近 10 年，還原除權息月報酬）】本月為 {this_m} 月，下個月為 {next_m} 月
{season_text()}

{etf_block}
【模型量化判斷（計分卡，已整合統計、技術、籌碼、基本面、估值與總經理論；AI 不可推翻）】
{model_block or "（無資料）"}
【隔天訊號（台股收盤後的國際行情與籌碼）】統計機率 {stat_p_1d:.0f}% → 綜合上漲機率 {nextday_p:.0f}%
{nextday_table.to_string(index=False) if not nextday_table.empty else "（無資料）"}

【台股收盤後的夜間新聞標題】
{news_text(night_news)}

【近 30 天潛在利空新聞標題（用利空關鍵字搜尋，需判斷是否真的與該股有關）】
{news_text(bad_news)}

【近 30 天產業展望 / 法說 / 營收新聞標題】
{news_text(outlook_news)}

【近 30 天成長題材新聞標題（訂單、產能、AI、機器人、地緣政治、供應鏈）】
{news_text(theme_news)}

【個股近 7 天新聞標題】
{news_text(stock_news)}

【國際財經近 7 天新聞標題】
{news_text(world_news)}
"""

    tab_overview, tab_ai, tab_forecast, tab_review, tab_tech, tab_compare = st.tabs(
        ["🏠 總覽", "🧠 AI 綜合評估", "🔮 未來走勢預估", "📝 每日檢討", "📐 技術分析", "📊 多股比較"])

    # ── 未來走勢預估 (量化部分；AI 預估在最後補上) ──
    with tab_forecast:
        st.subheader(f"🔮 {stock_name} 模型量化判斷")
        if scorecards:
            v_cols = st.columns(4, gap="small")
            for col, (h, c) in zip(v_cols, scorecards.items()):
                chg = (c["price"] / current_price - 1) * 100
                col.metric(f"{h}（信心：{c['confidence']}）", f"{c['direction']} {c['prob']:.0f}%",
                           delta=f"{chg:+.2f}% → {c['price']:.2f}")
                col.caption(f"±1σ 區間 {c['low']:.2f} ~ {c['high']:.2f}｜因子一致性 {c['agree'] * 100:.0f}%")
            st.caption("方向與機率完全由下方計分卡計算：相似情境統計機率 + 各因子加減分（百分點）。"
                       "上漲機率 ≥55% 判定上漲、≤45% 判定下跌；信心依機率偏離 50% 的程度與因子方向一致性決定。"
                       "不含人工或 AI 的主觀調整。")

            st.markdown("#### 🧮 各期間計分卡")
            h_tabs = st.tabs([f"{h}：{c['direction']} {c['prob']:.0f}%" for h, c in scorecards.items()])
            for h_tab, (h, c) in zip(h_tabs, scorecards.items()):
                with h_tab:
                    st.markdown(f"**{h}**：基礎機率 {c['base']:.0f}% ＋ 因子合計 {c['total']:+.1f} ＝ **上漲機率 {c['prob']:.0f}%**")
                    st.dataframe(
                        c["table"].style.apply(lambda r: [f"color: {'#E5484D' if r['加減分'] > 0 else '#30A46C' if r['加減分'] < 0 else MUTED}"
                                                          if col in ('判讀', '加減分') and pd.notna(r['加減分']) else "" for col in r.index], axis=1)
                                        .format({"加減分": lambda v: "—" if pd.isna(v) else f"{v:+.1f}"}),
                        width="stretch", hide_index=True)
                    if c.get("removed"):
                        st.caption("🚫 已從模型篩除（與未來報酬相關性低 / 回測不顯著）：" + "、".join(dict.fromkeys(c["removed"])))
            st.caption("紅 = 偏多、綠 = 偏空（台股慣例）。只保留經 20 年回測與實盤驗證「與未來報酬正相關且顯著」的因子；"
                       "每週一重新檢定，失效的因子會自動移除、重新變顯著的會自動加回。")

            macro_names = {"通貨膨脹（美國 CPI）", "利率循環（聯邦基金利率）", "實質利率", "殖利率曲線（10 年 − 2 年）",
                           "貨幣供給（美國 M2）", "股債風險溢酬（Fed Model）", "美元指數", "國際油價", "新台幣匯率"}
            macro_rows = [r for r in scorecards["1 年"]["removed_rows"] if r["因子"] in macro_names]
            if macro_rows:
                with st.expander("🌍 總體經濟環境（僅供參考：經 20 年回測對台股無顯著預測力，已從模型移除）"):
                    for r in macro_rows:
                        st.markdown(f"- **{r['因子']}**：{r['數據']}  \n  <small style='color:{MUTED}'>{r['理論依據']}</small>",
                                    unsafe_allow_html=True)

        if not nextday_table.empty:
            st.markdown("#### 🌙 隔天訊號面板")
            st.dataframe(nextday_table, width="stretch", hide_index=True)
            nd_removed = removed_nextday_factors()
            if nd_removed:
                st.caption("🚫 已篩除：" + "；".join(f"{FACTOR_LABELS.get(k, k)}（{v}）" for k, v in nd_removed.items()))
        if upcoming:
            st.markdown("#### 📅 未來兩週重要事件")
            for d_, text in upcoming:
                st.markdown(f"- **{d_:%m/%d}（{'一二三四五六日'[d_.weekday()]}）** {text}")
        if fundamentals:
            st.markdown("#### 🏭 基本面與籌碼")
            for f in fundamentals:
                st.markdown(f"- {f}")

        st.markdown("---")
        st.markdown("#### 📊 統計區間與價位參考")
        st.caption("以下為統計推估的「可能區間」，不是保證會到的價位，僅供參考。")

        if projection.empty:
            st.info("歷史資料不足，無法計算統計區間。")
        else:
            st.markdown(
                f"**📊 基準情境區間**（近 {history_years:.1f} 年資料，以目前股價 {current_price:.2f} 為基準）\n"
                "- 只取歷史上和現在**相似情境**（季線乖離位階相近、季線方向相同）的日子，看之後實際漲跌\n"
                "- **不照抄過去的大多頭，也不假設零成長**：先扣除該股過去的平均漲幅，再加回「一半用該股過去年化報酬、"
                "一半用市場長期平均 7%」的基準成長（上限 20%）\n"
                "- 「若延續過去趨勢」欄位是完全照過去走勢的版本，僅供對照")
            show = projection.drop(columns=["_days"]).copy()
            for col in ["悲觀 (10%)", "保守 (25%)", "中位數", "樂觀 (75%)", "極樂觀 (90%)", "若延續過去趨勢"]:
                show[col] = show[col].map(lambda p: f"{p:.2f}（{(p / current_price - 1) * 100:+.1f}%）")
            show["上漲機率 (%)"] = show["上漲機率 (%)"].round(0)
            show["基準年化成長 (%)"] = show["基準年化成長 (%)"].round(1)
            show["過去平均漲幅 (%)"] = show["過去平均漲幅 (%)"].round(1)
            st.dataframe(show, width="stretch", hide_index=True)

            # 扇形圖：近半年走勢 + 未來區間
            hist_px = df['Close'].iloc[-126:]
            last_day = hist_px.index[-1]
            fx = [last_day] + [last_day + pd.tseries.offsets.BDay(d) for d in projection["_days"]]
            band = lambda col: [current_price] + list(projection[col])
            fan = go.Figure()
            fan.add_trace(go.Scatter(x=hist_px.index, y=hist_px, name="收盤價", line=dict(color=TEXT, width=2)))
            fan.add_trace(go.Scatter(x=fx, y=band("極樂觀 (90%)"), line=dict(width=0), showlegend=False, hoverinfo="skip"))
            fan.add_trace(go.Scatter(x=fx, y=band("悲觀 (10%)"), name="10%~90% 區間", fill="tonexty",
                                     fillcolor="rgba(212,175,55,0.15)", line=dict(width=0)))
            fan.add_trace(go.Scatter(x=fx, y=band("樂觀 (75%)"), line=dict(width=0), showlegend=False, hoverinfo="skip"))
            fan.add_trace(go.Scatter(x=fx, y=band("保守 (25%)"), name="25%~75% 區間", fill="tonexty",
                                     fillcolor="rgba(212,175,55,0.35)", line=dict(width=0)))
            fan.add_trace(go.Scatter(x=fx, y=band("中位數"), name="中位數", mode="lines+markers",
                                     line=dict(color=GOLD, width=2, dash="dash")))
            st.plotly_chart(style_fig(fan, 420), width="stretch")

        sup_col, res_col, pe_col = st.columns(3, gap="small")
        with sup_col:
            st.markdown("**🟢 技術面支撐**")
            for v, k in supports:
                st.markdown(f"- {k}：**{v:.2f}**（{(v / current_price - 1) * 100:+.1f}%）")
        with res_col:
            st.markdown("**🔴 技術面壓力**")
            if not resistances:
                st.markdown("- 已創一年新高，上方無明顯壓力")
            for v, k in resistances:
                st.markdown(f"- {k}：**{v:.2f}**（{(v / current_price - 1) * 100:+.1f}%）")
        with pe_col:
            st.markdown("**💰 本益比推估合理價**")
            if not pe_targets:
                st.markdown("- 無本益比資料（ETF 或虧損）")
            for note in eps_notes:
                st.caption(f"📈 {note}")
            for k, (pe, p) in pe_targets.items():
                st.markdown(f"- {k}（{pe:.1f} 倍）→ **{p:.2f}**（{(p / current_price - 1) * 100:+.1f}%）")
            if pe_targets and not eps_notes:
                st.caption("目前只有近四季 EPS。到總覽按「載入近一年本益比區間」可算出 EPS 成長率，1 年合理價會更準。")

    # ── 總覽 ──
    with tab_overview:
        col1, col2, col3 = st.columns(3, gap="small")
        with col1:
            st.metric(label=f"當前股價 ({stock_name})", value=f"{current_price:.2f} 元", delta=f"{price_change:+.2f} ({price_pct:+.2f}%)")
        with col2:
            st.metric(label="KD 技術指標狀態", value=f"K:{current_k:.1f} / D:{current_d:.1f}", delta=kd_status, delta_color="normal")
        with col3:
            st.metric(label="60MA 季線乖離預警", value=f"{bias_60:+.2f}%", delta=bias_text, delta_color=bias_color)

        # 隔天 (統計 + 多因子訊號) / 隔週 (相似情境統計) 預估
        if not projection.empty:
            def direction_of(p):
                return "📈 預計上漲" if p >= 55 else "📉 預計下跌" if p <= 45 else "➡️ 多空接近，偏盤整"

            f1, f2 = st.columns(2, gap="small")
            # 隔天：由上漲機率反推預期漲跌 (常態近似：μ = σ × Φ⁻¹(p))
            sigma_1d = float(np.log(long_adj).diff().iloc[-60:].std())
            mu_1d = sigma_1d * NormalDist().inv_cdf(min(max(nextday_p, 1), 99) / 100)
            target_1d = current_price * np.exp(mu_1d)
            f1.metric("隔天預估（統計＋隔天訊號）", f"{direction_of(nextday_p)}（上漲機率 {nextday_p:.0f}%）",
                      delta=f"{(target_1d / current_price - 1) * 100:+.2f}%，預估 {target_1d:.2f}"
                            f"（±1σ {current_price * np.exp(mu_1d - sigma_1d):.2f} ~ {current_price * np.exp(mu_1d + sigma_1d):.2f}）")
            week = projection[projection["期間"] == "1 週"]
            if not week.empty:
                r = week.iloc[0]
                up_p = r["上漲機率 (%)"]
                f2.metric("隔週預估（5 個交易日）", f"{direction_of(up_p)}（上漲機率 {up_p:.0f}%）",
                          delta=f"{(r['中位數'] / current_price - 1) * 100:+.2f}%，中位數 {r['中位數']:.2f}"
                                f"（區間 {r['保守 (25%)']:.2f} ~ {r['樂觀 (75%)']:.2f}）")
            st.caption(f"隔天：歷史相似情境統計 {stat_p_1d:.0f}%，加上下方隔天訊號後為 {nextday_p:.0f}%。"
                       "隔週：相似情境統計。上漲機率 55% 以上標示上漲、45% 以下標示下跌；僅供參考。")

        if not nextday_table.empty:
            st.markdown("**🌙 隔天訊號面板**（美股、台積電 ADR、台指期夜盤、法人、融資、借券、大戶、營收、選擇權、夜間新聞）")
            st.dataframe(
                nextday_table.style.apply(lambda r: [f"color: {'#E5484D' if r['加減分'] > 0 else '#30A46C' if r['加減分'] < 0 else MUTED}"
                                                     if c in ('影響', '加減分') else "" for c in r.index], axis=1),
                width="stretch", hide_index=True)
            weight_meta = load_model_weights()["meta"]
            st.caption("加減分為對「隔天上漲機率」的影響（百分點，紅 = 偏多、綠 = 偏空，依台股慣例）；加減分 = 原始分數 × 權重。"
                       + (f"權重由每日檢討自動校準（最後更新 {weight_meta.get('updated', '—')}，累積 {weight_meta.get('samples', 0)} 筆驗證）。"
                          if weight_meta else "目前為預設權重，每日檢討累積資料後會自動校準。"))

        # 未來兩週的重要事件
        events = upcoming_events(stock_code, today.date())
        if events:
            st.markdown("**📅 未來兩週重要事件**")
            for d_, text in events:
                st.markdown(f"- **{d_:%m/%d}（{'一二三四五六日'[d_.weekday()]}）** {text}")
            if night_news:
                with st.expander(f"📰 台股收盤後的夜間新聞（{len(night_news)} 則）"):
                    show_news(night_news)

        st.subheader(f"🚦 {stock_name} 風險燈號：{risk['emoji']} {risk['level']}（分數 {risk['score']}）")
        if risk["items"]:
            for s, t, text in risk["items"]:
                st.markdown(f"- {'🔴' if s >= 2 else '🟡'} **[{t}]** {text}")
        else:
            st.markdown("- 🟢 目前沒有明顯的技術面或估值警訊")
        st.caption("分數：0-1 🟢 低風險｜2-3 🟡 留意｜4-5 🟠 警戒｜6 以上 🔴 高風險。乖離以該股自身近三年的歷史分布判斷，波動大的股票不會動不動就被判過熱。")

        if len(risk_board) > 1:
            st.markdown("**📋 所有選取股票風險總覽**")
            st.dataframe(risk_board, width="stretch", hide_index=True)

        st.subheader("💰 估值評估（證交所 / 櫃買中心官方資料）")
        if not own_val:
            st.caption("此標的沒有官方本益比資料（ETF 不適用本益比評估）。")
        else:
            v1, v2, v3 = st.columns(3, gap="small")
            with v1:
                if np.isnan(current_pe):
                    st.metric("本益比 (PER)", "—", delta="近四季虧損", delta_color="off")
                elif not np.isnan(peer_pe_median):
                    diff = (current_pe / peer_pe_median - 1) * 100
                    st.metric("本益比 (PER)", f"{current_pe:.2f} 倍",
                              delta=f"比同類股中位數 {peer_pe_median:.1f} 倍{'高' if diff > 0 else '低'} {abs(diff):.0f}%",
                              delta_color="inverse")
                else:
                    st.metric("本益比 (PER)", f"{current_pe:.2f} 倍")
            with v2:
                st.metric("股價淨值比 (PBR)", f"{own_val.get('股價淨值比', np.nan):.2f} 倍")
            with v3:
                st.metric("殖利率", f"{own_val.get('殖利率 (%)', np.nan):.2f}%")

            if stock_code.endswith(".TW") and not np.isnan(current_pe):
                if pe_hist.empty:
                    st.button("📈 載入近一年本益比區間（約 20 秒）",
                              on_click=lambda: st.session_state.update({pe_hist_key: True}))
                elif not np.isnan(pe_pct):
                    level = "偏低" if pe_pct < 25 else "偏高" if pe_pct > 75 else "合理"
                    st.markdown(f"近一年本益比 **{pe_hist.min():.1f} ~ {pe_hist.max():.1f} 倍**（平均 {pe_hist.mean():.1f}），"
                                f"目前 {current_pe:.1f} 倍位於 **{pe_pct:.0f} 百分位**，屬於歷史區間的 **{level}** 位置。")
                    pe_fig = go.Figure()
                    pe_fig.add_trace(go.Scatter(x=pe_hist.index, y=pe_hist, name="本益比", line=dict(color=GOLD, width=2)))
                    for q, label, color in [(0.25, "25 百分位", "#30A46C"), (0.5, "中位數", MUTED), (0.75, "75 百分位", "#E5484D")]:
                        pe_fig.add_hline(y=float(pe_hist.quantile(q)), line=dict(color=color, dash="dash", width=1),
                                         annotation_text=label, annotation_position="right")
                    st.plotly_chart(style_fig(pe_fig, 280), width="stretch")
            elif stock_code.endswith(".TWO"):
                st.caption("上櫃股票目前只提供當日本益比，暫無歷史區間。")

        st.subheader("🏭 基本面與籌碼")
        if revenue or profit or holders_now:
            b1, b2, b3 = st.columns(3, gap="small")
            if revenue:
                b1.metric(f"月營收年增率（{revenue['month']}）", f"{revenue['yoy']:+.1f}%",
                          delta=f"月增 {revenue['mom']:+.1f}%，今年累計 {revenue['cum_yoy']:+.1f}%")
            if profit:
                b2.metric(f"毛利率（{profit['quarter']}）", f"{profit['gross']:.2f}%",
                          delta=f"營益率 {profit['operating']:.2f}%，淨利率 {profit['net']:.2f}%", delta_color="off")
            if holders_now:
                b3.metric(f"千張大戶持股（{holders_now['date']}）", f"{holders_now['big1000']:.2f}%",
                          delta=f"散戶（<50 張）{holders_now['retail']:.2f}%", delta_color="off")
            if revenue and revenue["note"]:
                st.caption(f"📝 公司營收說明：{revenue['note']}")
            st.caption("毛利率趨勢、大戶週變化需要每日檢討累積資料後才會顯示；ETF 沒有營收與毛利率。")
        else:
            st.caption("此標的沒有月營收 / 財報資料（ETF 或資料尚未公布）。")

        st.subheader("📊 股價歷史波動圖")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df['Close'], name='收盤價', line=dict(color=GOLD, width=2)))
        fig.add_trace(go.Scatter(x=df.index, y=df['60MA'], name='60MA 季線', line=dict(color="#4C8DFF", dash='dash')))
        st.plotly_chart(style_fig(fig, 420), width="stretch")

        news_col, world_col = st.columns(2, gap="large")
        with news_col:
            st.subheader(f"📰 {stock_name} 近 7 天新聞")
            show_news(stock_news)
        with world_col:
            st.subheader("🌏 國際財經新聞")
            show_news(world_news[:8])

        if is_etf:
            st.subheader(f"🧺 {stock_name} 成分股分析")
            if etf_top.empty:
                st.caption("⚠️ " + (etf_errors[0] if etf_errors else "暫時抓不到此 ETF 的持股明細。"))
            else:
                st.caption(f"持股資料日期 {etf_date}（投信每月公布），共 {len(etf_holdings)} 檔成分股。「持股增減」為與上期相比的持股股數變化。")
                e1, e2, e3 = st.columns(3, gap="small")
                e1.metric("前十大權重合計", f"{etf_top['權重 (%)'].sum():.1f}%",
                          delta=f"最大 {etf_top.iloc[0]['成分股']} {etf_top.iloc[0]['權重 (%)']:.1f}%", delta_color="off")
                e2.metric("前十大近一月加權貢獻", f"{contrib:+.2f} 個百分點")
                e3.metric("亮警戒 / 高風險的權重", f"{risky_weight:.1f}%",
                          delta="成分股轉弱" if risky_weight >= 20 else "成分股大致穩定",
                          delta_color="inverse" if risky_weight >= 20 else "off")
                st.dataframe(etf_top, width="stretch", hide_index=True)
                if etf_errors:
                    st.caption("⚠️ 部分成分股資料抓取失敗：" + "；".join(etf_errors[:5]))
                if not big_changes.empty:
                    st.markdown("**持股增減幅度較大（±5% 以上）的成分股：** " +
                                "、".join(f"{r['成分股']} {r['持股增減']}" for _, r in big_changes.head(10).iterrows()))
                with st.expander(f"查看全部 {len(etf_holdings)} 檔成分股"):
                    st.dataframe(etf_holdings, width="stretch", hide_index=True)
            if etf_news:
                st.markdown("**📰 近 90 天成分股調整新聞**")
                show_news(etf_news)

        bad_col, outlook_col = st.columns(2, gap="large")
        with bad_col:
            st.subheader("⚠️ 近 30 天潛在利空新聞")
            st.caption("用「下修、衰退、砍單、賣超…」等關鍵字搜尋，部分可能與該股無直接關係。")
            show_news(bad_news)
        with outlook_col:
            st.subheader("🔭 產業展望 / 法說 / 營收")
            show_news(outlook_news)

        st.subheader("🚀 近 30 天成長題材新聞（訂單、產能、AI、機器人、地緣政治）")
        show_news(theme_news)

    # ── 技術分析 ──
    with tab_tech:
        c1, c2, c3 = st.columns(3, gap="small")
        with c1:
            st.metric("RSI (14)", f"{current_rsi:.1f}", delta=rsi_text, delta_color=rsi_color)
        with c2:
            st.metric("MACD (12, 26, 9)", f"DIF:{current_dif:.2f} / MACD:{current_macd:.2f}", delta=macd_text, delta_color=macd_color)
        with c3:
            st.metric("布林通道 (20, 2σ)", f"{last['BB_LOW']:.1f} ~ {last['BB_UP']:.1f}", delta=bb_text, delta_color="off")

        tech_fig = make_subplots(
            rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
            row_heights=[0.46, 0.18, 0.18, 0.18],
            subplot_titles=("股價 + 布林通道", "MACD", "RSI (14)", "KD (9)"),
        )
        tech_fig.add_trace(go.Scatter(x=df.index, y=df['BB_UP'], name='布林上軌', line=dict(color=MUTED, width=1)), row=1, col=1)
        tech_fig.add_trace(go.Scatter(x=df.index, y=df['BB_LOW'], name='布林下軌', line=dict(color=MUTED, width=1),
                                      fill='tonexty', fillcolor='rgba(138,150,173,0.10)'), row=1, col=1)
        tech_fig.add_trace(go.Scatter(x=df.index, y=df['BB_MID'], name='布林中軌', line=dict(color=MUTED, dash='dot', width=1)), row=1, col=1)
        tech_fig.add_trace(go.Scatter(x=df.index, y=df['Close'], name='收盤價', line=dict(color=GOLD, width=2)), row=1, col=1)

        osc_colors = np.where(df['OSC'] >= 0, "#E5484D", "#30A46C")
        tech_fig.add_trace(go.Bar(x=df.index, y=df['OSC'], name='OSC', marker_color=osc_colors), row=2, col=1)
        tech_fig.add_trace(go.Scatter(x=df.index, y=df['DIF'], name='DIF', line=dict(color=GOLD)), row=2, col=1)
        tech_fig.add_trace(go.Scatter(x=df.index, y=df['MACD'], name='MACD', line=dict(color="#4C8DFF")), row=2, col=1)

        tech_fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], name='RSI', line=dict(color="#9B6BFF")), row=3, col=1)
        tech_fig.add_hline(y=70, line=dict(color="#E5484D", dash="dash", width=1), row=3, col=1)
        tech_fig.add_hline(y=30, line=dict(color="#30A46C", dash="dash", width=1), row=3, col=1)

        tech_fig.add_trace(go.Scatter(x=df.index, y=df['K'], name='K', line=dict(color=GOLD)), row=4, col=1)
        tech_fig.add_trace(go.Scatter(x=df.index, y=df['D'], name='D', line=dict(color="#4C8DFF")), row=4, col=1)

        style_fig(tech_fig, 900)
        tech_fig.update_layout(showlegend=False)
        st.plotly_chart(tech_fig, width="stretch")

    # ── 多股比較 ──
    with tab_compare:
        if len(checked_codes) < 2:
            st.info("請在左側點選 2 檔以上的股票，即可疊圖比較走勢。")
        else:
            mode = st.radio("比較方式", ["累積報酬率 (%)", "股價 (元)"], horizontal=True)
            palette = [GOLD, "#4C8DFF", "#2EC4A6", "#9B6BFF", "#FF8A4C", "#E5484D", "#30A46C", "#8A96AD"]
            cmp_fig = go.Figure()
            summary = []
            for i, code in enumerate(checked_codes):
                cdf = get_price_data(code)
                if cdf.empty:
                    st.warning(f"{ALL_STOCKS[code]} ({code}) 查無資料")
                    continue
                close = cdf['Close']
                # 報酬率用還原除權息價 (含息報酬)，股價則顯示交易所原始收盤價
                adj = cdf['Adj Close'].fillna(close) if 'Adj Close' in cdf.columns else close
                ret = (adj / adj.iloc[0] - 1) * 100
                y = ret if mode.startswith("累積") else close
                cmp_fig.add_trace(go.Scatter(x=close.index, y=y, name=f"{ALL_STOCKS[code]} ({code})",
                                             line=dict(color=palette[i % len(palette)], width=2)))
                summary.append({
                    "股票": f"{ALL_STOCKS[code]} ({code})",
                    "產業": CODE_TO_CATEGORY[code],
                    "最新收盤": round(float(close.iloc[-1]), 2),
                    "近一年含息報酬率 (%)": round(float(ret.iloc[-1]), 2),
                    "最大回撤 (%)": round(float(((adj / adj.cummax()) - 1).min() * 100), 2),
                })
            if mode.startswith("累積"):
                cmp_fig.add_hline(y=0, line=dict(color=MUTED, dash="dot", width=1))
            st.plotly_chart(style_fig(cmp_fig, 460), width="stretch")
            if summary:
                st.dataframe(pd.DataFrame(summary), width="stretch", hide_index=True)

    # ── AI 綜合評估 (放在最後執行，等待 AI 時不會卡住其他分頁) ──
    with tab_ai:
        st.subheader(f"🧠 {stock_name} 綜合評估報告")
        st.caption(f"使用 {ai_provider} / {model_choice}。資料涵蓋新聞時事、國際局勢、技術分析、月份效應與估值；同一檔股票每天只自動產生一次，不重複計費。")

        report_key = f"report_v7_{stock_code}_{today:%Y%m%d}_{model_choice}"
        c_auto, c_regen = st.columns([3, 1])
        auto_report = c_auto.toggle("選到股票時自動產生報告", value=True, key="auto_report")
        regenerate = c_regen.button("🔄 重新產生", width="stretch")

        etf_prompt = ("\n   ## 🧺 ETF 成分股分析\n"
                      "   （這是 ETF，請評估前十大成分股的表現與風險燈號、集中度風險、持股增減與成分股調整新聞，"
                      "說明成分股變化對 ETF 未來走勢的影響；成分股中亮警戒 / 高風險的權重越高，ETF 下跌風險越大）") if is_etf else ""

        report_prompt = f"""你是資深台股研究員，請根據下方「即時資料」為 {stock_name}（{stock_code}）撰寫一份專業的綜合評估報告。

撰寫規則：
1. 使用繁體中文與 Markdown，依序輸出以下章節，每個章節用條列逐項寫出評語，並在章節最後標註「**評等：偏多 / 中性 / 偏空**」三選一：
   ## 📰 一、新聞時事
   （挑出與該股相關的重要新聞逐條點評利多或利空，無關的新聞略過；「潛在利空新聞」與「產業展望」兩區都要逐條檢視，
    真正相關的利空一定要列出，不可略過）{etf_prompt}
   ## 🌏 二、國際局勢
   （美股、費半、利率、匯率、VIX、地緣政治與關稅等對該股的影響）
   ## 📐 三、技術分析
   （趨勢與均線、KD、RSI、MACD、布林通道、量能、乖離，並指出支撐與壓力價位）
   ## 📅 四、月份效應
   （本月與下個月的歷史表現與可能的季節性原因，例如除權息、財報、法說會、作帳行情）
   ## 💰 五、估值與位階
   （本益比與同類股中位數比較、近一年本益比百分位、股價淨值比、殖利率、52 週位階；缺資料就說明無法評估，ETF 改評估殖利率與位階）
   ## ⚠️ 六、主要風險
   ## 🐂 多方觀點：為何看好？
   （扮演看多的分析師，具體說明「為什麼市場看好這檔股票」，至少 3 點，逐點從以下角度檢視（不適用的略過）：
    - 業績與訂單能見度：訂單是否已排到未來 1-2 年、產能是否滿載、營收與毛利趨勢、法說會指引
    - 產業需求：例如 AI 晶片、高效能運算、伺服器、電力設備等需求是否持續成長
    - 技術領先與護城河：製程 / 技術 / 市占的領先程度，對手追趕難度
    - 地緣政治與戰爭：是「風險」還是「受惠」要說清楚（例如供應鏈重組、國防需求、各國補貼設廠，或反過來的戰爭斷鏈風險）
    每點標註時間軸（短期 / 中期 / 長期）、可能性（高 / 中 / 低）與要追蹤的觀察指標（例如月營收、法說指引、資本支出））
   ## 🚀 長期產業趨勢（3-10 年）
   （評估這檔股票在長期大趨勢中的位置：
    - AI 持續進步 → AI 代理、人形機器人、自動駕駛、智慧工廠大量落地，是否形成如「工業革命」般的新時代
    - 在這條產業鏈中，這家公司扮演什麼角色（例如晶片製造、零組件、組裝、電力、材料），受惠程度（高 / 中 / 低）與原因
    - 這些趨勢大約何時開始反映在營收上、可能帶來多大的成長空間（用情境描述，不要捏造具體數字）
    - 同時點出這個長期題材「可能不如預期」的情況（技術瓶頸、需求泡沫、被替代）
    標註：此節可運用一般產業知識，但要註明「產業背景知識，非即時資料」）
   ## 🐻 空方觀點與長期隱憂
   （扮演看空的分析師，至少列出 3 點「這檔股票可能會跌、甚至長期沒落」的理由，涵蓋：
    產業趨勢是否轉弱或被新技術取代、競爭對手與削價、客戶集中與砍單、獲利與毛利下滑、估值過高、
    政策 / 關稅 / 地緣政治、籌碼（外資賣超、融資過高）。每點說明可能性（高 / 中 / 低）與觀察指標。
    若認為看空理由比看多理由更有力，要明確說出來）
   ## 🔮 七、未來走勢預估
   （表格：期間 | 方向 | 上漲機率 | 預估價 | ±1σ 區間 | 信心 | 主要依據；期間分成隔天、1 週、1 個月、1 年四列。
    方向、上漲機率、預估價、區間、信心「必須直接採用」【模型量化判斷】的數值，不得依個人看法修改或推翻；
    你的任務是用專業語言解釋模型為何得出這個結論（引用計分卡中影響最大的因子與其理論依據），
    並補充模型未涵蓋的風險。若你認為模型可能有盲點，放在表格下方的「模型限制」段落說明，但不改變結論。
    表格下方逐期說明，並列出「若跌破 X 則轉弱、若站上 Y 則轉強」的關鍵價位（取自支撐與壓力）。）
2. 最後輸出「## 🧾 總結」：
   - 先用表格列出「面向 | 評等 | 一句話評語」
   - 用「⚖️ 多空對照」表格並列最重要的 3 個看多理由與 3 個看空理由，說明哪一方目前比較有力
   - 再給出整體評等（偏多 / 中性 / 偏空）與信心程度（高 / 中 / 低）
   - 用一句話總結 1 週、1 個月、1 年的基準預估價
3. 即時數字（股價、指標、估值、新聞）只能根據提供的資料，不要捏造數字或新聞內容；新聞只有標題，判讀時要保守。
   產業背景、商業模式、長期趨勢可以運用一般知識，但要標註為背景知識，不可編造具體的訂單金額、營收數字或未公開消息。
   價格預估是機率性的推估，要說明不確定性。
   保持多空中立：過去 10 年台股處於大多頭，歷史數據天生偏多，評估時要主動修正這個偏誤，不要因為「過去一直漲」就推論未來會漲。
4. 結尾加一行：「以上為資料彙整與分析，非投資建議，請自行判斷風險。」

即時資料：
{data_snapshot}"""

        if not api_key:
            st.warning(f"請先在左側欄輸入 {ai_provider} API Key，選到股票時就會自動產生綜合評估報告。")
        elif regenerate or (auto_report and report_key not in st.session_state):
            with st.spinner(f"AI 正在綜合評估 {stock_name}（約 30-60 秒）..."):
                try:
                    st.session_state[report_key] = ask_ai(ai_provider, api_key, model_choice, report_prompt, max_tokens=8000)
                except Exception as e:
                    st.error(f"AI 連線失敗，請檢查 API Key 是否正確。錯誤代碼: {str(e)}")
        elif report_key not in st.session_state:
            if st.button("🚀 產生綜合評估報告"):
                with st.spinner(f"AI 正在綜合評估 {stock_name}（約 30-60 秒）..."):
                    try:
                        st.session_state[report_key] = ask_ai(ai_provider, api_key, model_choice, report_prompt, max_tokens=8000)
                    except Exception as e:
                        st.error(f"AI 連線失敗，請檢查 API Key 是否正確。錯誤代碼: {str(e)}")

        if report_key in st.session_state:
            with st.container(border=True):
                st.markdown(st.session_state[report_key])

        with st.expander("📦 本次評估使用的資料"):
            st.markdown("**🌏 國際與大盤指標**")
            if market_ctx.empty:
                st.caption("暫時抓不到國際指標。")
            else:
                st.dataframe(market_ctx, width="stretch", hide_index=True)
            st.markdown(f"**📅 月份效應（近 10 年）** — 本月 {this_m} 月、下個月 {next_m} 月")
            if seasonality.empty:
                st.caption("暫時抓不到歷史月資料。")
            else:
                st.dataframe(
                    seasonality.style.apply(lambda r: ["background-color: rgba(212,175,55,0.25)" if r.name in (this_m, next_m) else "" for _ in r], axis=1),
                    width="stretch", hide_index=True,
                )
            st.markdown("**💰 估值**")
            st.write(valuation or "暫時抓不到估值資料。")

        st.markdown("---")
        st.subheader("💬 追問 AI")
        user_input = st.text_area("✍️ 貼上您看到的最新法說會、新聞或您的成本帳面問題：", height=120, placeholder="例如：這檔股票外資最近狂賣，我成本套在套牢高點，現在黃金交叉該加碼嗎？")

        if st.button("🚀 送出提問"):
            if not api_key:
                st.warning(f"請先在左側欄輸入您的 {ai_provider} API Key 才能開通大腦功能。")
            else:
                with st.spinner("AI 正在調閱大盤籌碼與位階數據..."):
                    try:
                        prompt_context = (
                            f"你是精通台股的財經專家。以下是 {stock_name} 的即時資料：\n{data_snapshot}\n"
                            + (f"先前的綜合評估報告：\n{st.session_state[report_key]}\n" if report_key in st.session_state else "")
                            + f"用戶提問：{user_input}\n請根據這些資料，用繁體中文給予客觀的分析與建議，並提醒風險。"
                        )
                        ai_reply = ask_ai(ai_provider, api_key, model_choice, prompt_context)
                        st.markdown("### 💡 AI 回覆：")
                        st.write(ai_reply)
                    except Exception as e:
                        st.error(f"AI 連線失敗，請檢查 API Key 是否正確。錯誤代碼: {str(e)}")

    # ── 未來走勢預估：補上 AI 綜合預估 (取自綜合評估報告第七節) ──
    with tab_forecast:
        st.markdown("---")
        st.markdown("### 🧠 AI 解讀（解釋模型結論，不改變方向與機率）")
        report = st.session_state.get(report_key, "")
        start = report.find("## 🔮")
        if start >= 0:
            end = report.find("\n## ", start + 5)
            with st.container(border=True):
                st.markdown(report[start:end if end > 0 else None].replace("## 🔮 七、未來走勢預估", "", 1))
        elif report:
            st.caption("AI 報告中沒有找到預估段落，請到「🧠 AI 綜合評估」查看完整報告或按「🔄 重新產生」。")
        else:
            st.caption("輸入 API Key 後，AI 綜合評估報告會一併產生 1 週 / 1 個月 / 1 年的預估。")

        # 預估背後的理由：看多 / 長期趨勢 / 看空
        for marker, title in [("## 🐂", "🐂 為何看好？"), ("## 🚀", "🚀 長期產業趨勢（3-10 年）"), ("## 🐻", "🐻 空方觀點與長期隱憂")]:
            s = report.find(marker)
            if s < 0:
                continue
            e = report.find("\n## ", s + 5)
            section = report[s:e if e > 0 else None]
            with st.expander(title, expanded=True):
                st.markdown(section.split("\n", 1)[1] if "\n" in section else section)
        st.caption("⚠️ 以上為統計與 AI 推估，股價受突發事件影響很大，實際走勢可能完全不同，請勿作為買賣依據。")

    # ── 每日檢討 (GitHub Actions 每個交易日早上自動執行 daily_review.py，結果存在 data/) ──
    with tab_review:
        st.subheader("📝 每日檢討與模型自我校準")
        horizon_info = load_horizon_model()
        if horizon_info:
            st.markdown("**📊 20 年回測校準結果（每週一自動重跑）**")
            st.dataframe(pd.DataFrame([
                {"期間": h, "採用": "✅ 回測校準模型" if hm["mode"] == "model" else "⛔ 無預測力，改用歷史上漲比例",
                 "驗證期（2016 起）命中率": f"{hm['test_hit'] * 100:.1f}%", "永遠猜漲": f"{hm['test_always_up'] * 100:.1f}%",
                 "Brier（模型 / 歷史比例）": f"{hm['test_brier']:.4f} / {hm['test_brier_base']:.4f}"}
                for h, hm in horizon_info.items() if "test_hit" in hm]), width="stretch", hide_index=True)
        bt_report = DATA_DIR / "backtest" / "report.md"
        if bt_report.exists():
            with st.expander("📄 完整 20 年回測報告"):
                st.markdown(bt_report.read_text(encoding="utf-8"))
        review_dir = DATA_DIR / "reviews"
        reviews = sorted(review_dir.glob("*.md"), reverse=True) if review_dir.exists() else []
        if not reviews:
            st.info("尚未有每日檢討。GitHub Actions 會在台灣時間週一到週五 07:00（開盤前）自動執行，"
                    "也可以到 GitHub → Actions → 每日檢討 → Run workflow 手動執行；執行完成後這裡就會顯示結果。")
        else:
            pred_file = DATA_DIR / "predictions.csv"
            preds = pd.read_csv(pred_file, dtype={"code": str}) if pred_file.exists() else pd.DataFrame()
            done = preds.dropna(subset=["ret_1d"]) if "ret_1d" in preds.columns else pd.DataFrame()
            if not done.empty:
                done = done[(done["final_p"] != 50) & (done["ret_1d"] != 0)].sort_values("target_date").copy()
                done["命中"] = (done["final_p"] > 50) == (done["ret_1d"] > 0)
                mine = done[done["code"] == stock_code]
                r1, r2, r3, r4 = st.columns(4, gap="small")
                r1.metric("模型累積命中率", f"{done['命中'].mean() * 100:.1f}%", delta=f"{len(done)} 筆驗證", delta_color="off")
                r2.metric("永遠猜漲（對照）", f"{(done['ret_1d'] > 0).mean() * 100:.1f}%",
                          delta=f"模型 {(done['命中'].mean() - (done['ret_1d'] > 0).mean()) * 100:+.1f} 個百分點")
                stat_hit = ((done["stat_p"] > 50) == (done["ret_1d"] > 0)).mean()
                r3.metric("只用統計（對照）", f"{stat_hit * 100:.1f}%",
                          delta=f"加入訊號後 {(done['命中'].mean() - stat_hit) * 100:+.1f} 個百分點")
                r4.metric(f"{stock_name} 命中率", f"{mine['命中'].mean() * 100:.1f}%" if not mine.empty else "—",
                          delta=f"{len(mine)} 筆", delta_color="off")

                daily = done.groupby("target_date")["命中"].agg(["sum", "size"])
                daily["累積命中率"] = daily["sum"].cumsum() / daily["size"].cumsum() * 100
                daily["當日命中率"] = daily["sum"] / daily["size"] * 100
                hit_fig = go.Figure()
                hit_fig.add_trace(go.Bar(x=daily.index, y=daily["當日命中率"], name="當日命中率", marker_color="rgba(212,175,55,0.35)"))
                hit_fig.add_trace(go.Scatter(x=daily.index, y=daily["累積命中率"], name="累積命中率", line=dict(color=GOLD, width=2)))
                hit_fig.add_hline(y=50, line=dict(color=MUTED, dash="dot", width=1), annotation_text="50%（擲硬幣）")
                st.plotly_chart(style_fig(hit_fig, 300), width="stretch")

                if not mine.empty:
                    with st.expander(f"{stock_name} 的歷次預測與結果"):
                        st.dataframe(mine[["base_date", "target_date", "stat_p", "final_p", "ret_1d", "命中"]].rename(columns={
                            "base_date": "依據收盤日", "target_date": "驗證日", "stat_p": "統計機率 (%)",
                            "final_p": "預測上漲機率 (%)", "ret_1d": "實際漲跌 (%)"}).iloc[::-1],
                            width="stretch", hide_index=True)
            else:
                st.caption("預測已開始累積，下一個交易日之後就會有命中率統計。")

            weight_info = load_model_weights()["meta"]
            if weight_info.get("factor_stats"):
                st.markdown(f"**⚖️ 目前的因子權重**（最後校準 {weight_info.get('updated', '—')}，整體機率縮放 {weight_info.get('scale', 1.0)}）")
                st.dataframe(pd.DataFrame([
                    {"因子": FACTOR_LABELS.get(k, k), "命中率（含歷史回填）": f"{s['acc'] * 100:.1f}%" if s.get("acc") is not None else "—",
                     "樣本": s.get("n", 0), "實盤樣本": s.get("live_n", 0), "權重": s.get("weight", 1.0)}
                    for k, s in weight_info["factor_stats"].items()]).sort_values("樣本", ascending=False),
                    width="stretch", hide_index=True)
                st.caption("命中率 50% 的因子權重為 1.0；越準權重越高（最高 2），越不準越接近 0（等於停用）。樣本少時會向 1.0 收斂，避免被少數幾天誤導。")

            pick = st.selectbox("📅 檢討報告日期", [p.stem for p in reviews])
            with st.container(border=True):
                st.markdown((review_dir / f"{pick}.md").read_text(encoding="utf-8"))
except Exception as main_e:
    st.error(f"數據載入失敗，可能因 Yahoo 網路阻擋，請重新整理網頁。錯誤原因: {str(main_e)}")
