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
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

# 頁面基本設定
st.set_page_config(page_title="台股 AI 戰情室", layout="wide")

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

st.title("📈 2026 台股熱門爆量標的 AI 戰情室")

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


def _to_float(value):
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return np.nan


@st.cache_data(ttl=600, show_spinner=False)
def load_official_quotes():
    """從證交所 / 櫃買中心 OpenAPI 取得全市場最新一個交易日的官方收盤行情 (10 分鐘更新一次)"""
    pattern = re.compile(r"^(\d{4}|00\d{2,4}[A-Z]?)$")  # 一般股票與 ETF，排除權證
    quotes = {}
    sources = [
        ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", ".TW", "上市",
         {"code": "Code", "name": "Name", "Open": "OpeningPrice", "High": "HighestPrice",
          "Low": "LowestPrice", "Close": "ClosingPrice", "Volume": "TradeVolume"}),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", ".TWO", "上櫃",
         {"code": "SecuritiesCompanyCode", "name": "CompanyName", "Open": "Open", "High": "High",
          "Low": "Low", "Close": "Close", "Volume": "TradingShares"}),
    ]
    for url, suffix, board, f in sources:
        try:
            for row in requests.get(url, timeout=15).json():
                code = row.get(f["code"], "").strip()
                if not pattern.match(code):
                    continue
                roc = row.get("Date", "")  # 民國年，例如 1150922
                quotes[code + suffix] = {
                    "name": row.get(f["name"], "").strip(),
                    "board": board,
                    "Date": pd.Timestamp(int(roc[:-4]) + 1911, int(roc[-4:-2]), int(roc[-2:])),
                    **{k: _to_float(row.get(f[k])) for k in ["Open", "High", "Low", "Close", "Volume"]},
                }
        except Exception:
            continue
    return quotes


def load_market_list():
    return {code: (q["name"], q["board"]) for code, q in load_official_quotes().items()}


# 證交所 / 櫃買中心產業別代碼
INDUSTRY_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
    "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造業",
    "15": "航運業", "16": "觀光餐旅", "17": "金融保險業", "18": "貿易百貨業", "19": "綜合", "20": "其他業",
    "21": "化學工業", "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業",
    "26": "光電業", "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務", "35": "綠能環保",
    "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "91": "存託憑證",
}
INDUSTRY_ICONS = {
    "半導體業": "🔬", "電腦及週邊設備業": "🖥️", "光電業": "💡", "通信網路業": "📡", "電子零組件業": "🔌",
    "電子通路業": "📦", "資訊服務業": "💻", "其他電子業": "🔧", "金融保險業": "💰", "航運業": "🚢",
    "鋼鐵工業": "🏗️", "生技醫療業": "💊", "電機機械": "⚙️", "汽車工業": "🚗", "食品工業": "🍜",
    "塑膠工業": "🧪", "化學工業": "🧪", "建材營造業": "🏠", "綠能環保": "🌱", "油電燃氣業": "⛽", "ETF": "🏦",
}


@st.cache_data(ttl=86400, show_spinner=False)
def load_industry_map():
    """公司代號 → 產業別名稱 (證交所 / 櫃買中心公司基本資料)"""
    industries = {}
    sources = [
        ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", ".TW", "公司代號", "產業別"),
        ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", ".TWO", "SecuritiesCompanyCode", "SecuritiesIndustryCode"),
    ]
    for url, suffix, code_key, ind_key in sources:
        try:
            for row in requests.get(url, timeout=30).json():
                ind = str(row.get(ind_key, "")).strip().zfill(2)
                industries[str(row.get(code_key, "")).strip() + suffix] = INDUSTRY_NAMES.get(ind, "其他")
        except Exception:
            continue
    return industries


def industry_of(code):
    if code.split('.')[0].startswith("00"):
        return "ETF"
    return load_industry_map().get(code, "其他")


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
for code, name in WATCH["custom"].items():  # 新增的股票依產業自動分類
    STOCK_POOL.setdefault(classify(code), {})[code] = name
ALL_STOCKS = {code: name for pool in STOCK_POOL.values() for code, name in pool.items()}
CODE_TO_CATEGORY = {code: cat for cat, pool in STOCK_POOL.items() for code in pool}


def add_to_watchlist():
    codes = [(c, MARKET[c][0]) for c in st.session_state.get("add_pick", [])]
    manual = st.session_state.get("add_manual", "").strip().upper()
    if manual:
        code = manual if "." in manual else manual + st.session_state.get("add_board", ".TW")
        codes.append((code, st.session_state.get("add_manual_name", "").strip() or code))
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
    if c_yes.button("確定移除", type="primary", use_container_width=True):
        remove_codes([code])
        st.rerun()
    if c_no.button("取消", use_container_width=True):
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
                      use_container_width=True)
        if c_del.button("−", key=f"del_{code}", help=f"移除 {name}", use_container_width=True):
            confirm_remove(code)

checked_codes = [c for c in ALL_STOCKS if st.session_state.get(f"chk_{c}", False)]
stock_code = None
if checked_codes:
    st.sidebar.button(f"取消全部選取（目前 {len(checked_codes)} 檔）", on_click=clear_checks, use_container_width=True)
    st.sidebar.markdown("---")
    stock_code = st.sidebar.selectbox("🔎 主分析標的", checked_codes, format_func=lambda c: f"{ALL_STOCKS[c]} ({c})")
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
    st.text_input("股票名稱 (選填)", key="add_manual_name", placeholder="例如：聯發科")
    st.radio("市場", [".TW", ".TWO"], key="add_board", horizontal=True,
             format_func=lambda s: "上市 (.TW)" if s == ".TW" else "上櫃 (.TWO)")
    st.caption("新增後會依證交所 / 櫃買中心的產業別自動歸類（例如聯發科 → 半導體類、ETF → 大盤市值與高股息）。")
    st.button("加入並選取", type="primary", on_click=add_to_watchlist, use_container_width=True)
    if WATCH["hidden"]:
        st.button(f"↩️ 還原被移除的預設股票（{len(WATCH['hidden'])} 檔）", on_click=restore_defaults, use_container_width=True)

# AI設定區
st.sidebar.markdown("---")
st.sidebar.header("🤖 AI 窗口設定")
ai_provider = st.sidebar.selectbox("選擇 AI 供應商", list(AI_PROVIDERS.keys()))
api_key = st.sidebar.text_input(f"輸入 {ai_provider} API Key", type="password", help=f"請輸入您的 {ai_provider} API 金鑰")
model_choice = st.sidebar.selectbox("選擇 AI 模型", AI_PROVIDERS[ai_provider])


# 資料抓取
@st.cache_data(ttl=300, max_entries=30, show_spinner=False)
def load_data(code):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    # auto_adjust=False：Close 為交易所原始收盤價，Adj Close 為還原除權息價
    df = yf.download(code, start=start_date, end=end_date, auto_adjust=False)
    # 移除多層索引 (yfinance v0.2+ 新版防錯)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def get_price_data(code):
    """Yahoo 歷史資料 + 交易所官方最新行情校正 (Yahoo 偶爾會缺某天或數字不同)"""
    df = load_data(code).dropna(subset=['Close']).copy()
    q = load_official_quotes().get(code)
    if q is None or np.isnan(q["Close"]) or df.empty:
        return df
    cols = ["Open", "High", "Low", "Close", "Volume"]
    is_new = q["Date"] not in df.index
    df.loc[q["Date"], cols] = [q[c] for c in cols]
    if is_new and "Adj Close" in df.columns:
        df.loc[q["Date"], "Adj Close"] = q["Close"]
    return df.sort_index()


def _flatten(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def load_news(query, limit=8, days=7):
    """Google 新聞 RSS：近 N 天新聞標題 (免 API Key)"""
    try:
        resp = requests.get(
            "https://news.google.com/rss/search",
            params={"q": f"{query} when:{days}d", "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
        )
        root = ET.fromstring(resp.content)
    except Exception:
        return []
    news = []
    for item in root.iter("item"):
        title = item.findtext("title", "").strip()
        source = item.findtext("source", "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(TAIPEI).strftime("%m/%d %H:%M")
        except Exception:
            published = ""
        news.append({"title": title, "source": source, "date": published, "link": item.findtext("link", "")})
        if len(news) >= limit:
            break
    return news


@st.cache_data(ttl=86400, show_spinner=False)
def load_etf_holdings(code):
    """MoneyDJ ETF 持股明細：成分股、權重、持股增減 (投信每月公布)"""
    try:
        html = requests.get("https://www.moneydj.com/ETF/X/Basic/Basic0007a.xdjhtm", params={"etfid": code},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=15).text
    except Exception:
        return pd.DataFrame(), ""
    pos = html.find('id="Repeater1"')
    dates = re.findall(r"資料日期：(\d{4}/\d{2}/\d{2})", html[:pos] if pos > 0 else html)
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for table_id in ("Repeater1", "Repeater2"):
        table = soup.find("table", id=table_id)
        if table is None:
            continue
        for tr in table.find_all("tr"):
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) >= 4 and tds[0]:
                rows.append({"成分股": tds[0], "持股 (千股)": _to_float(tds[1]),
                             "權重 (%)": _to_float(tds[2]), "持股增減": tds[3]})
    holdings = pd.DataFrame(rows)
    if not holdings.empty:
        holdings = holdings.sort_values("權重 (%)", ascending=False).reset_index(drop=True)
    return holdings, (dates[-1] if dates else "")


MARKET_INDICES = {
    "^TWII": "台股加權指數",
    "^SOX": "費城半導體指數",
    "^IXIC": "那斯達克指數",
    "^GSPC": "S&P 500",
    "^VIX": "VIX 恐慌指數",
    "^TNX": "美 10 年債殖利率",
    "TWD=X": "美元兌台幣",
}


@st.cache_data(ttl=1800, show_spinner=False)
def load_market_context():
    """國際與大盤指標近期表現"""
    try:
        raw = yf.download(list(MARKET_INDICES), period="3mo", auto_adjust=False, progress=False)
        closes = raw["Close"]
    except Exception:
        return pd.DataFrame()
    rows = []
    for sym, name in MARKET_INDICES.items():
        if sym not in closes.columns:
            continue
        s = closes[sym].dropna()
        if len(s) < 22:
            continue
        pct = lambda n: (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100
        rows.append({"指標": name, "最新": round(float(s.iloc[-1]), 2), "日漲跌 (%)": round(float(pct(1)), 2),
                     "近一週 (%)": round(float(pct(5)), 2), "近一月 (%)": round(float(pct(21)), 2)})
    return pd.DataFrame(rows)


@st.cache_data(ttl=86400, show_spinner=False)
def load_seasonality(code):
    """近 10 年各月份的平均報酬與上漲機率 (還原除權息)"""
    try:
        df = _flatten(yf.download(code, period="10y", interval="1mo", auto_adjust=False, progress=False))
        px = df["Adj Close"].dropna()
    except Exception:
        return pd.DataFrame()
    ret = px.pct_change().dropna() * 100
    this_month = pd.Timestamp(datetime.now(TAIPEI).date()).replace(day=1)
    ret = ret[ret.index < this_month]  # 排除尚未走完的當月
    if ret.empty:
        return pd.DataFrame()
    g = ret.groupby(ret.index.month)
    return pd.DataFrame({
        "月份": [f"{m} 月" for m in g.mean().index],
        "平均報酬 (%)": g.mean().round(2).values,
        "中位數 (%)": g.median().round(2).values,
        "上漲機率 (%)": (g.apply(lambda x: (x > 0).mean()) * 100).round(0).values,
        "樣本年數": g.count().values,
    }, index=g.mean().index)


@st.cache_data(ttl=86400, show_spinner=False)
def load_long_history(code):
    """近 10 年日線 (還原除權息)，用於歷史報酬分布"""
    try:
        df = _flatten(yf.download(code, period="10y", auto_adjust=False, progress=False))
        return df["Adj Close"].dropna()
    except Exception:
        return pd.Series(dtype=float)


FORECAST_HORIZONS = {"隔天": 1, "1 週": 5, "1 個月": 21, "1 年": 252}


MARKET_LONG_RETURN = 0.07  # 股市長期平均年報酬的保守假設


def compute_projection(adj, price):
    """未來價格區間 (不直接外推過去多頭，也不假設零成長)：
    1. 相似情境：只取歷史上「季線乖離百分位相近 (±15%) 且季線方向相同」的日子，看它們之後的實際表現
    2. 基準報酬：先扣除該股過去的平均漲幅，再加回「一半用該股過去年化報酬、一半用市場長期 7%」
       的基準成長 (上限 20%)，成長股保有合理成長、又不會把過去大多頭照抄到未來
    """
    rows = []
    years = len(adj) / 252
    hist_annual = float((adj.iloc[-1] / adj.iloc[0]) ** (1 / years) - 1) if years > 1 else MARKET_LONG_RETURN
    base_annual = float(np.clip(0.5 * hist_annual + 0.5 * MARKET_LONG_RETURN, 0, 0.20))
    log_ret = np.log(adj).diff().dropna()
    sigma_d = float(log_ret.iloc[-60:].std()) if len(log_ret) >= 60 else np.nan
    ma60 = adj.rolling(60).mean()
    bias_rank = (adj / ma60 - 1).rank(pct=True)
    slope_up = ma60.diff(20) > 0
    similar = ((bias_rank - bias_rank.iloc[-1]).abs() <= 0.15) & (slope_up == slope_up.iloc[-1])
    for label, h in FORECAST_HORIZONS.items():
        fwd_all = (adj.shift(-h) / adj - 1).dropna()
        if len(fwd_all) < max(h * 2, 60):
            continue
        drift = float(fwd_all.mean())
        fwd_sim = fwd_all[similar.reindex(fwd_all.index, fill_value=False)]
        use_sim = len(fwd_sim) >= max(30, h)
        fwd = fwd_sim if use_sim else fwd_all
        base_h = (1 + base_annual) ** (h / 252) - 1
        neutral = (1 + fwd) / (1 + drift) * (1 + base_h) - 1
        q = neutral.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
        sig = sigma_d * np.sqrt(h)
        rows.append({
            "期間": label,
            "悲觀 (10%)": price * (1 + q[0.10]),
            "保守 (25%)": price * (1 + q[0.25]),
            "中位數": price * (1 + q[0.50]),
            "樂觀 (75%)": price * (1 + q[0.75]),
            "極樂觀 (90%)": price * (1 + q[0.90]),
            "上漲機率 (%)": float((neutral > 0).mean() * 100),
            "若延續過去趨勢": price * (1 + float(fwd.median())),
            "過去平均漲幅 (%)": drift * 100,
            "基準年化成長 (%)": base_annual * 100,
            "情境樣本": f"{len(fwd_sim)} 天" if use_sim else "不足，改用全部",
            "目前波動 ±1σ": f"{price * np.exp(-sig):.2f} ~ {price * np.exp(sig):.2f}" if not np.isnan(sig) else "—",
            "_days": h,
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=3600, show_spinner=False)
def load_official_valuation():
    """證交所 / 櫃買中心官方公布的全市場本益比、殖利率、股價淨值比 (每日更新)"""
    vals = {}
    sources = [
        ("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", ".TW",
         {"code": "Code", "pe": "PEratio", "yield": "DividendYield", "pb": "PBratio"}),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", ".TWO",
         {"code": "SecuritiesCompanyCode", "pe": "PriceEarningRatio", "yield": "YieldRatio", "pb": "PriceBookRatio"}),
    ]
    for url, suffix, f in sources:
        try:
            for row in requests.get(url, timeout=15).json():
                pe = _to_float(row.get(f["pe"]))
                vals[row.get(f["code"], "").strip() + suffix] = {
                    "本益比": pe if pe > 0 else np.nan,  # 虧損公司不列本益比
                    "殖利率 (%)": _to_float(row.get(f["yield"])),
                    "股價淨值比": _to_float(row.get(f["pb"])),
                }
        except Exception:
            continue
    return vals


@st.cache_data(ttl=86400, show_spinner="正在向證交所載入近一年本益比（約 20 秒）...")
def load_pe_history(stock_no, months=12):
    """證交所個股每日本益比 (近 N 個月，僅上市股票)；證交所有流量限制，每次請求間隔 1.5 秒"""
    now = datetime.now(TAIPEI)
    points = {}
    for i in range(months):
        y, m = now.year, now.month - i
        if m <= 0:
            y, m = y - 1, m + 12
        try:
            j = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU",
                             params={"date": f"{y}{m:02d}01", "stockNo": stock_no, "response": "json"}, timeout=15).json()
            fields = j.get("fields", [])
            di, pi = fields.index("日期"), fields.index("本益比")
            for r in j.get("data", []):
                roc_y, mm, dd = map(int, re.findall(r"\d+", r[di]))
                pe = _to_float(r[pi])
                if pe > 0:
                    points[pd.Timestamp(roc_y + 1911, mm, dd)] = pe
        except Exception:
            pass
        time.sleep(1.5)
    return pd.Series(points, dtype=float).sort_index()


@st.cache_data(ttl=86400, show_spinner=False)
def load_yahoo_extras(code):
    """Yahoo 補充資料：市值與 Beta (抓不到就略過)"""
    try:
        info = yf.Ticker(code).info
    except Exception:
        return {}
    result = {}
    if isinstance(info.get("marketCap"), (int, float)):
        result["市值 (億元)"] = round(info["marketCap"] / 1e8, 0)
    if isinstance(info.get("beta"), (int, float)):
        result["Beta"] = round(float(info["beta"]), 2)
    if isinstance(info.get("forwardEps"), (int, float)) and info["forwardEps"] > 0:
        result["預估 EPS（分析師共識）"] = round(float(info["forwardEps"]), 2)
    return result


def add_indicators(df):
    df = df.copy()
    close = df['Close']

    # 60MA 季線
    df['60MA'] = close.rolling(window=60).mean()

    # KD (9 日)
    low_min = df['Low'].rolling(window=9).min()
    high_max = df['High'].rolling(window=9).max()
    rsv = ((close - low_min) / (high_max - low_min) * 100).fillna(50)
    k_vals, d_vals = [], []
    k, d = 50.0, 50.0
    for r in rsv:
        k = (2/3) * k + (1/3) * r
        d = (2/3) * d + (1/3) * k
        k_vals.append(k)
        d_vals.append(d)
    df['K'] = k_vals
    df['D'] = d_vals

    # RSI (14 日, Wilder 平滑)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - 100 / (1 + rs)).fillna(100).where(gain.notna())

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['DIF'] = ema12 - ema26
    df['MACD'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['OSC'] = df['DIF'] - df['MACD']

    # 布林通道 (20 日, 2 倍標準差)
    df['BB_MID'] = close.rolling(window=20).mean()
    bb_std = close.rolling(window=20).std()
    df['BB_UP'] = df['BB_MID'] + 2 * bb_std
    df['BB_LOW'] = df['BB_MID'] - 2 * bb_std
    return df


def cross_signal(fast, slow):
    """回傳最新一根 K 棒的交叉狀態：golden / death / None"""
    if fast.iloc[-1] > slow.iloc[-1] and fast.iloc[-2] <= slow.iloc[-2]:
        return "golden"
    if fast.iloc[-1] < slow.iloc[-1] and fast.iloc[-2] >= slow.iloc[-2]:
        return "death"
    return None


RISK_LEVELS = [(6, "🔴", "高風險"), (4, "🟠", "警戒"), (2, "🟡", "留意"), (0, "🟢", "低風險")]


def assess_risk(d, adj, pe_pct=np.nan, pe_vs_peer=np.nan):
    """多指標風險評估：乖離以「自身近三年歷史」為基準，不同波動的股票才有可比性"""
    last = d.iloc[-1]
    price = float(last['Close'])
    items = []  # (分數, 類型, 說明)

    # 季線乖離與自身歷史百分位
    bias = float((price / last['60MA'] - 1) * 100)
    bias_pct = np.nan
    if len(adj) > 300:
        a = adj.iloc[-756:]
        hist_bias = ((a / a.rolling(60).mean() - 1) * 100).dropna()
        bias_pct = float((hist_bias < bias).mean() * 100)
    pct_txt = f"，位於自身近三年 {bias_pct:.0f} 百分位" if not np.isnan(bias_pct) else ""
    if bias_pct >= 90 or bias > 25:
        items.append((2, "過熱", f"季線乖離 {bias:+.1f}%{pct_txt}，漲多易拉回"))
        bias_text = "🔥 高檔過熱！請勿追高"
    elif bias_pct >= 80 or bias > 15:
        items.append((1, "過熱", f"季線乖離 {bias:+.1f}%{pct_txt}，位階偏高"))
        bias_text = "⚠️ 位階偏熱，留意拉回"
    elif bias_pct <= 10 or bias < -15:
        items.append((2, "弱勢", f"季線乖離 {bias:+.1f}%{pct_txt}，跌深但趨勢偏弱"))
        bias_text = "🚨 極端超跌，趨勢偏弱"
    elif bias_pct <= 20 or bias < -10:
        items.append((1, "弱勢", f"季線乖離 {bias:+.1f}%{pct_txt}，位階偏低"))
        bias_text = "⚠️ 位階偏低，觀察止跌"
    else:
        bias_text = "➡️ 位階正常"
    if not np.isnan(bias_pct):
        bias_text += f"（近三年 {bias_pct:.0f} 百分位）"

    # RSI / KD / MACD
    rsi, k = float(last['RSI']), float(last['K'])
    if rsi > 80:
        items.append((2, "過熱", f"RSI {rsi:.0f} 嚴重超買"))
    elif rsi > 70:
        items.append((1, "過熱", f"RSI {rsi:.0f} 超買"))
    elif rsi < 30:
        items.append((1, "弱勢", f"RSI {rsi:.0f} 超賣，賣壓沉重"))
    if cross_signal(d['K'], d['D']) == "death":
        items.append((2 if k > 80 else 1, "轉弱", f"KD 死亡交叉{'（高檔）' if k > 80 else ''}"))
    if cross_signal(d['DIF'], d['MACD']) == "death":
        items.append((1, "轉弱", "MACD 死亡交叉"))
    if last['DIF'] < 0 and last['MACD'] < 0:
        items.append((1, "空頭", "MACD 位於零軸下方"))

    # 趨勢：均線
    ma20 = d['Close'].rolling(20).mean()
    ma60_slope = float(d['60MA'].iloc[-1] - d['60MA'].iloc[-21]) if len(d) > 80 else 0.0
    if price < last['60MA'] and ma60_slope < 0:
        items.append((2, "空頭", "跌破季線且季線下彎"))
    elif price < last['60MA']:
        items.append((1, "空頭", "股價在季線之下"))
    if price < ma20.iloc[-1] < last['60MA']:
        items.append((1, "空頭", "短中期均線空頭排列"))

    # 自高點回落幅度
    drawdown = (price / float(d['High'].max()) - 1) * 100
    if drawdown < -35:
        items.append((2, "弱勢", f"距一年高點已回落 {drawdown:.0f}%"))
    elif drawdown < -20:
        items.append((1, "弱勢", f"距一年高點已回落 {drawdown:.0f}%"))

    # 波動度異常
    if len(adj) > 300:
        vol = np.log(adj.iloc[-756:]).diff().rolling(20).std().dropna()
        if len(vol) > 60 and vol.median() > 0 and vol.iloc[-1] / vol.median() > 1.5:
            items.append((1, "波動", f"波動度升至平常的 {vol.iloc[-1] / vol.median():.1f} 倍"))

    # 量價
    vol_ratio = float(last['Volume'] / d['Volume'].iloc[-21:-1].mean()) if d['Volume'].iloc[-21:-1].mean() > 0 else 1.0
    day_chg = float((price / d['Close'].iloc[-2] - 1) * 100)
    if vol_ratio > 2 and day_chg < -2:
        items.append((2, "賣壓", f"爆量下跌（量增 {vol_ratio:.1f} 倍、跌 {day_chg:.1f}%）"))
    elif vol_ratio > 2 and rsi > 70:
        items.append((1, "過熱", f"爆量追價（量增 {vol_ratio:.1f} 倍）"))

    # 布林通道
    if price >= last['BB_UP']:
        items.append((1, "過熱", "觸及布林上軌"))
    elif price <= last['BB_LOW']:
        items.append((1, "弱勢", "跌破布林下軌"))

    # 估值
    if pe_pct >= 80:
        items.append((1, "估值", f"本益比位於近一年 {pe_pct:.0f} 百分位"))
    if pe_vs_peer >= 50:
        items.append((1, "估值", f"本益比比同類股中位數高 {pe_vs_peer:.0f}%"))

    score = sum(s for s, _, _ in items)
    emoji, level = next((e, l) for th, e, l in RISK_LEVELS if score >= th)
    items.sort(key=lambda x: -x[0])
    return {"score": score, "emoji": emoji, "level": level, "items": items,
            "bias": bias, "bias_pct": bias_pct, "bias_text": bias_text}


def ask_ai(provider, key, model, prompt, max_tokens=1500):
    if provider == "Claude":
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        url = "https://api.anthropic.com/v1/messages"
    else:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }
        url = "https://api.openai.com/v1/chat/completions"

    response = requests.post(url, headers=headers, json=payload, timeout=180)
    res_json = response.json()
    if response.status_code != 200:
        err = res_json.get("error", {})
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise RuntimeError(f"HTTP {response.status_code}: {msg}")

    if provider == "Claude":
        return "".join(block.get("text", "") for block in res_json["content"] if block.get("type") == "text")
    return res_json['choices'][0]['message']['content']


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
    etf_holdings, etf_date, etf_top, etf_summary = pd.DataFrame(), "", pd.DataFrame(), ""
    if is_etf:
        etf_holdings, etf_date = load_etf_holdings(stock_code)
    if not etf_holdings.empty:
        name_to_code = {name: code for code, (name, _) in MARKET.items()}
        top_rows = []
        for _, h in etf_holdings.head(10).iterrows():
            code = name_to_code.get(h["成分股"])
            row = {"成分股": h["成分股"], "權重 (%)": h["權重 (%)"], "持股增減": h["持股增減"],
                   "近一月 (%)": np.nan, "燈號": "—", "主要警訊": ""}
            if code:
                try:
                    hd = add_indicators(get_price_data(code))
                    row["近一月 (%)"] = round(float((hd['Close'].iloc[-1] / hd['Close'].iloc[-22] - 1) * 100), 1)
                    hr = assess_risk(hd, load_long_history(code))
                    row["燈號"] = f"{hr['emoji']} {hr['level']}"
                    row["主要警訊"] = "、".join(s for _, _, s in hr["items"][:2]) or "無"
                except Exception:
                    pass
            top_rows.append(row)
        etf_top = pd.DataFrame(top_rows)
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

    tab_overview, tab_ai, tab_forecast, tab_tech, tab_compare = st.tabs(
        ["🏠 總覽", "🧠 AI 綜合評估", "🔮 未來走勢預估", "📐 技術分析", "📊 多股比較"])

    # ── 未來走勢預估 (量化部分；AI 預估在最後補上) ──
    with tab_forecast:
        st.subheader(f"🔮 {stock_name} 未來 1 週 / 1 個月 / 1 年價格預估")
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
            st.dataframe(show, use_container_width=True, hide_index=True)

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
            st.plotly_chart(style_fig(fan, 420), use_container_width=True)

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

        # 隔天 / 隔週預估 (相似情境統計，詳見「🔮 未來走勢預估」)
        if not projection.empty:
            f1, f2 = st.columns(2, gap="small")
            for col, (label, period) in zip((f1, f2), [("隔天", "隔天"), ("1 週", "隔週（5 個交易日）")]):
                row = projection[projection["期間"] == label]
                if row.empty:
                    continue
                r = row.iloc[0]
                up_p = r["上漲機率 (%)"]
                chg = (r["中位數"] / current_price - 1) * 100
                if up_p >= 55:
                    direction = "📈 預計上漲"
                elif up_p <= 45:
                    direction = "📉 預計下跌"
                else:
                    direction = "➡️ 多空接近，偏盤整"
                col.metric(f"{period}預估", f"{direction}（上漲機率 {up_p:.0f}%）",
                           delta=f"{chg:+.2f}%，中位數 {r['中位數']:.2f}（區間 {r['保守 (25%)']:.2f} ~ {r['樂觀 (75%)']:.2f}）")
            st.caption("依歷史「相似情境」統計推估，短期漲跌接近擲硬幣，上漲機率 55% 以上才標示上漲、45% 以下才標示下跌；僅供參考。")

        st.subheader(f"🚦 {stock_name} 風險燈號：{risk['emoji']} {risk['level']}（分數 {risk['score']}）")
        if risk["items"]:
            for s, t, text in risk["items"]:
                st.markdown(f"- {'🔴' if s >= 2 else '🟡'} **[{t}]** {text}")
        else:
            st.markdown("- 🟢 目前沒有明顯的技術面或估值警訊")
        st.caption("分數：0-1 🟢 低風險｜2-3 🟡 留意｜4-5 🟠 警戒｜6 以上 🔴 高風險。乖離以該股自身近三年的歷史分布判斷，波動大的股票不會動不動就被判過熱。")

        if len(risk_board) > 1:
            st.markdown("**📋 所有選取股票風險總覽**")
            st.dataframe(risk_board, use_container_width=True, hide_index=True)

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
                    st.plotly_chart(style_fig(pe_fig, 280), use_container_width=True)
            elif stock_code.endswith(".TWO"):
                st.caption("上櫃股票目前只提供當日本益比，暫無歷史區間。")

        st.subheader("📊 股價歷史波動圖")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df['Close'], name='收盤價', line=dict(color=GOLD, width=2)))
        fig.add_trace(go.Scatter(x=df.index, y=df['60MA'], name='60MA 季線', line=dict(color="#4C8DFF", dash='dash')))
        st.plotly_chart(style_fig(fig, 420), use_container_width=True)

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
                st.caption("暫時抓不到此 ETF 的持股明細。")
            else:
                st.caption(f"持股資料日期 {etf_date}（投信每月公布），共 {len(etf_holdings)} 檔成分股。「持股增減」為與上期相比的持股股數變化。")
                e1, e2, e3 = st.columns(3, gap="small")
                e1.metric("前十大權重合計", f"{etf_top['權重 (%)'].sum():.1f}%",
                          delta=f"最大 {etf_top.iloc[0]['成分股']} {etf_top.iloc[0]['權重 (%)']:.1f}%", delta_color="off")
                e2.metric("前十大近一月加權貢獻", f"{contrib:+.2f} 個百分點")
                e3.metric("亮警戒 / 高風險的權重", f"{risky_weight:.1f}%",
                          delta="成分股轉弱" if risky_weight >= 20 else "成分股大致穩定",
                          delta_color="inverse" if risky_weight >= 20 else "off")
                st.dataframe(etf_top, use_container_width=True, hide_index=True)
                if not big_changes.empty:
                    st.markdown("**持股增減幅度較大（±5% 以上）的成分股：** " +
                                "、".join(f"{r['成分股']} {r['持股增減']}" for _, r in big_changes.head(10).iterrows()))
                with st.expander(f"查看全部 {len(etf_holdings)} 檔成分股"):
                    st.dataframe(etf_holdings, use_container_width=True, hide_index=True)
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
        st.plotly_chart(tech_fig, use_container_width=True)

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
            st.plotly_chart(style_fig(cmp_fig, 460), use_container_width=True)
            if summary:
                st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)

    # ── AI 綜合評估 (放在最後執行，等待 AI 時不會卡住其他分頁) ──
    with tab_ai:
        st.subheader(f"🧠 {stock_name} 綜合評估報告")
        st.caption(f"使用 {ai_provider} / {model_choice}。資料涵蓋新聞時事、國際局勢、技術分析、月份效應與估值；同一檔股票每天只自動產生一次，不重複計費。")

        report_key = f"report_v5_{stock_code}_{today:%Y%m%d}_{model_choice}"
        c_auto, c_regen = st.columns([3, 1])
        auto_report = c_auto.toggle("選到股票時自動產生報告", value=True, key="auto_report")
        regenerate = c_regen.button("🔄 重新產生", use_container_width=True)

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
   （綜合技術面、基本面、歷史報酬分布、月份效應與國際局勢，用表格列出：
    期間 | 預估區間（低～高） | 基準預估價 | 方向（偏漲 / 盤整 / 偏跌） | 信心（高 / 中 / 低） | 主要依據
    期間分成 1 週、1 個月、1 年三列。
    - 1 週：以技術面支撐壓力、目前波動度為主
    - 1 個月：技術面趨勢 + 月份效應 + 新聞與國際局勢
    - 1 年：以本益比推估合理價（優先用未來一年 EPS，成長股不可只用近四季 EPS）、歷史 1 年報酬分布與產業前景為主
    「基準情境區間」已把過去的大多頭漲幅換成合理的基準成長（該股歷史與市場 7% 各半），是預估的起點；「若延續過去趨勢」只是對照，不可直接當作預估。
    不可預設看漲：依據利多與利空的強弱決定方向，技術面轉弱、估值偏高、利空新聞或產業趨勢不利時，應給出「偏跌」並寫出下跌目標價。
    表格下方逐期說明推論過程，並列出「若跌破 X 則轉弱、若站上 Y 則轉強」的關鍵價位。）
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
                st.dataframe(market_ctx, use_container_width=True, hide_index=True)
            st.markdown(f"**📅 月份效應（近 10 年）** — 本月 {this_m} 月、下個月 {next_m} 月")
            if seasonality.empty:
                st.caption("暫時抓不到歷史月資料。")
            else:
                st.dataframe(
                    seasonality.style.apply(lambda r: ["background-color: rgba(212,175,55,0.25)" if r.name in (this_m, next_m) else "" for _ in r], axis=1),
                    use_container_width=True, hide_index=True,
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
        st.markdown("### 🧠 AI 綜合預估（技術面 + 基本面 + 歷史 + 國際局勢）")
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
except Exception as main_e:
    st.error(f"數據載入失敗，可能因 Yahoo 網路阻擋，請重新整理網頁。錯誤原因: {str(main_e)}")
