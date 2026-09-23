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
        border-radius: 14px;
        padding: 20px 22px;
        margin-bottom: 12px;
        box-shadow: 0 4px 14px rgba(0,0,0,0.25);
    }}
    div[data-testid="stMetricLabel"] p {{ font-size: 0.95rem; color: {MUTED}; }}
    div[data-testid="stMetricValue"] {{ font-size: 1.75rem; font-weight: 600; color: {TEXT}; padding: 6px 0; }}
    div[data-testid="stMetricDelta"] {{ font-size: 0.92rem; }}

    /* 分頁樣式 */
    .stTabs [data-baseweb="tab-list"] {{ gap: 8px; }}
    .stTabs [data-baseweb="tab"] {{
        background: {NAVY_LIGHT}; border-radius: 10px 10px 0 0; padding: 10px 22px; color: {MUTED};
    }}
    .stTabs [aria-selected="true"] {{ color: {GOLD} !important; border-bottom: 2px solid {GOLD} !important; }}

    .stButton > button {{ background: {GOLD}; color: {NAVY}; border: none; font-weight: 600; }}
    .stButton > button:hover {{ background: {GOLD_SOFT}; color: {NAVY}; }}

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
CUSTOM_CATEGORY = "⭐ 自選股"

# 產業類別的 icon 與色塊
CATEGORY_STYLE = {
    "半導體先進封裝": ("🔬", "#4C8DFF"),
    "AI伺服器代工群": ("🖥️", "#9B6BFF"),
    "光電與重電綠能": ("⚡", "#2EC4A6"),
    "大盤市值與高股息": ("🏦", GOLD),
    CUSTOM_CATEGORY: ("⭐", "#FF8A4C"),
}

# 自選股存檔 (重新啟動後仍保留)
WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"


def load_watchlist():
    try:
        return json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


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


if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist()

STOCK_POOL[CUSTOM_CATEGORY] = st.session_state.watchlist
ALL_STOCKS = {code: name for pool in STOCK_POOL.values() for code, name in pool.items()}
CODE_TO_CATEGORY = {code: cat for cat, pool in STOCK_POOL.items() for code in pool}


def add_to_watchlist():
    for code in st.session_state.get("add_pick", []):
        if code not in ALL_STOCKS:
            st.session_state.watchlist[code] = MARKET[code][0]
        st.session_state[f"chk_{code}"] = True
    manual = st.session_state.get("add_manual", "").strip().upper()
    if manual:
        code = manual if "." in manual else manual + st.session_state.get("add_board", ".TW")
        if code not in ALL_STOCKS:
            st.session_state.watchlist[code] = st.session_state.get("add_manual_name", "").strip() or code
        st.session_state[f"chk_{code}"] = True
    save_watchlist(st.session_state.watchlist)
    st.session_state.add_pick = []
    st.session_state.add_manual = ""
    st.session_state.add_manual_name = ""


def remove_from_watchlist(code):
    st.session_state.watchlist.pop(code, None)
    st.session_state.pop(f"chk_{code}", None)
    save_watchlist(st.session_state.watchlist)


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
st.sidebar.caption("勾選的股票才會抓資料與分析；未勾選的只是備用清單，不占資源。")

# 新增股票
MARKET = load_market_list()
with st.sidebar.expander("➕ 新增股票到自選股"):
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
    st.button("加入並勾選", on_click=add_to_watchlist, use_container_width=True)

# 各類別勾選清單
for cat, pool in STOCK_POOL.items():
    if not pool:
        continue
    icon, color = CATEGORY_STYLE[cat]
    n_checked = sum(st.session_state.get(f"chk_{c}", False) for c in pool)
    title = cat if cat.startswith(icon) else f"{icon} {cat}"
    count = f'<span class="cat-count">已勾選 {n_checked}</span>' if n_checked else ""
    st.sidebar.html(f'<div class="cat-header" style="border-left-color:{color};">{title}{count}</div>')
    for code, name in pool.items():
        if cat == CUSTOM_CATEGORY:
            c_chk, c_del = st.sidebar.columns([5, 1])
            c_chk.checkbox(f"{name} ({code})", key=f"chk_{code}")
            c_del.button("🗑️", key=f"del_{code}", on_click=remove_from_watchlist, args=(code,), help="從自選股移除")
        else:
            st.sidebar.checkbox(f"{name} ({code})", key=f"chk_{code}")

checked_codes = [c for c in ALL_STOCKS if st.session_state.get(f"chk_{c}", False)]
stock_code = None
if checked_codes:
    st.sidebar.button("取消全部勾選", on_click=clear_checks, use_container_width=True)
    st.sidebar.markdown("---")
    stock_code = st.sidebar.selectbox("🔎 主分析標的", checked_codes, format_func=lambda c: f"{ALL_STOCKS[c]} ({c})")
    stock_name = ALL_STOCKS[stock_code]
    category = CODE_TO_CATEGORY[stock_code]
    cat_icon, cat_color = CATEGORY_STYLE[category]
    st.sidebar.html(f"""<div class="sector-badge">
    <div class="swatch" style="background:{cat_color};"></div>
    <div class="icon">{cat_icon}</div>
    <div><div class="name">{stock_name} <span class="sub">{stock_code}</span></div>
    <div class="sub" style="color:{cat_color};">{category}</div></div>
</div>""")

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
def load_news(query, limit=8):
    """Google 新聞 RSS：近 7 天新聞標題 (免 API Key)"""
    try:
        resp = requests.get(
            "https://news.google.com/rss/search",
            params={"q": f"{query} when:7d", "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
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


# 待機模式：沒有勾選任何股票就不抓資料
if not checked_codes:
    st.info("💤 目前為待機模式：請在左側勾選想分析的股票（勾選 2 檔以上可使用多股比較）。")
    st.caption(f"備用清單共 {len(ALL_STOCKS)} 檔，未勾選的股票不會下載資料。")
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

    # 乖離狀態
    if bias_60 < -10:
        bias_text, bias_color = "🚨 極端超跌！砸坑機會", "inverse"
    elif bias_60 > 15:
        bias_text, bias_color = "⚠️ 高檔過熱！請勿追高", "off"
    else:
        bias_text, bias_color = "➡️ 軌道合理！紀律操作", "normal"

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
    market_ctx = load_market_context()
    seasonality = load_seasonality(stock_code)
    today = datetime.now(TAIPEI)

    # 估值：官方本益比 / 殖利率 / 股價淨值比 + 同類股比較 + 近一年本益比區間
    official_vals = load_official_valuation()
    own_val = official_vals.get(stock_code, {})
    current_pe = own_val.get("本益比", np.nan)
    peer_pes = [official_vals.get(c, {}).get("本益比", np.nan) for c in STOCK_POOL.get(category, {}) if c != stock_code]
    peer_pes = [p for p in peer_pes if not np.isnan(p)]
    peer_pe_median = float(np.median(peer_pes)) if category != CUSTOM_CATEGORY and len(peer_pes) >= 2 else np.nan

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

【估值】{"、".join(f"{k} {v}" for k, v in valuation.items()) or "（無資料）"}

【國際與大盤指標】
{market_ctx.to_string(index=False) if not market_ctx.empty else "（無資料）"}

【月份效應（近 10 年，還原除權息月報酬）】本月為 {this_m} 月，下個月為 {next_m} 月
{season_text()}

【個股近 7 天新聞標題】
{news_text(stock_news)}

【國際財經近 7 天新聞標題】
{news_text(world_news)}
"""

    tab_overview, tab_ai, tab_tech, tab_compare = st.tabs(["🏠 總覽", "🧠 AI 綜合評估", "📐 技術分析", "📊 多股比較"])

    # ── 總覽 ──
    with tab_overview:
        col1, col2, col3 = st.columns(3, gap="large")
        with col1:
            st.metric(label=f"當前股價 ({stock_name})", value=f"{current_price:.2f} 元", delta=f"{price_change:+.2f} ({price_pct:+.2f}%)")
        with col2:
            st.metric(label="KD 技術指標狀態", value=f"K:{current_k:.1f} / D:{current_d:.1f}", delta=kd_status, delta_color="normal")
        with col3:
            st.metric(label="60MA 季線乖離預警", value=f"{bias_60:+.2f}%", delta=bias_text, delta_color=bias_color)

        st.subheader("💰 估值評估（證交所 / 櫃買中心官方資料）")
        if not own_val:
            st.caption("此標的沒有官方本益比資料（ETF 不適用本益比評估）。")
        else:
            v1, v2, v3 = st.columns(3, gap="large")
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

    # ── 技術分析 ──
    with tab_tech:
        c1, c2, c3 = st.columns(3, gap="large")
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
            st.info("請在左側勾選 2 檔以上的股票，即可疊圖比較走勢。")
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

        report_key = f"report_{stock_code}_{today:%Y%m%d}_{model_choice}"
        c_auto, c_regen = st.columns([3, 1])
        auto_report = c_auto.toggle("選到股票時自動產生報告", value=True, key="auto_report")
        regenerate = c_regen.button("🔄 重新產生", use_container_width=True)

        report_prompt = f"""你是資深台股研究員，請根據下方「即時資料」為 {stock_name}（{stock_code}）撰寫一份專業的綜合評估報告。

撰寫規則：
1. 使用繁體中文與 Markdown，依序輸出以下章節，每個章節用條列逐項寫出評語，並在章節最後標註「**評等：偏多 / 中性 / 偏空**」三選一：
   ## 📰 一、新聞時事
   （挑出與該股相關的重要新聞逐條點評利多或利空，無關的新聞略過）
   ## 🌏 二、國際局勢
   （美股、費半、利率、匯率、VIX、地緣政治與關稅等對該股的影響）
   ## 📐 三、技術分析
   （趨勢與均線、KD、RSI、MACD、布林通道、量能、乖離，並指出支撐與壓力價位）
   ## 📅 四、月份效應
   （本月與下個月的歷史表現與可能的季節性原因，例如除權息、財報、法說會、作帳行情）
   ## 💰 五、估值與位階
   （本益比與同類股中位數比較、近一年本益比百分位、股價淨值比、殖利率、52 週位階；缺資料就說明無法評估，ETF 改評估殖利率與位階）
   ## ⚠️ 六、主要風險
2. 最後輸出「## 🧾 總結」：
   - 先用表格列出「面向 | 評等 | 一句話評語」
   - 再給出整體評等（偏多 / 中性 / 偏空）與信心程度（高 / 中 / 低）
   - 列出短線（1-2 週）與中線（1-3 個月）的觀察重點與關鍵價位
3. 只能根據提供的資料推論，不要捏造數字或新聞內容；新聞只有標題，判讀時要保守。
4. 結尾加一行：「以上為資料彙整與分析，非投資建議，請自行判斷風險。」

即時資料：
{data_snapshot}"""

        if not api_key:
            st.warning(f"請先在左側欄輸入 {ai_provider} API Key，選到股票時就會自動產生綜合評估報告。")
        elif regenerate or (auto_report and report_key not in st.session_state):
            with st.spinner(f"AI 正在綜合評估 {stock_name}（約 30-60 秒）..."):
                try:
                    st.session_state[report_key] = ask_ai(ai_provider, api_key, model_choice, report_prompt, max_tokens=4000)
                except Exception as e:
                    st.error(f"AI 連線失敗，請檢查 API Key 是否正確。錯誤代碼: {str(e)}")
        elif report_key not in st.session_state:
            if st.button("🚀 產生綜合評估報告"):
                with st.spinner(f"AI 正在綜合評估 {stock_name}（約 30-60 秒）..."):
                    try:
                        st.session_state[report_key] = ask_ai(ai_provider, api_key, model_choice, report_prompt, max_tokens=4000)
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
except Exception as main_e:
    st.error(f"數據載入失敗，可能因 Yahoo 網路阻擋，請重新整理網頁。錯誤原因: {str(main_e)}")
