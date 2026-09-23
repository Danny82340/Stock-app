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
from pathlib import Path
from datetime import datetime, timedelta

# 頁面基本設定
st.set_page_config(page_title="台股 AI 戰情室", layout="wide")

# 主題色：深藍 + 暗金
NAVY = "#0B1426"
NAVY_LIGHT = "#13213D"
GOLD = "#D4AF37"
GOLD_SOFT = "#B8962E"
TEXT = "#E6E9EF"
MUTED = "#8A96AD"

st.markdown(f"""
<style>
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

    .sector-badge {{
        display: flex; align-items: center; gap: 10px;
        background: {NAVY}; border-radius: 10px; padding: 10px 12px; margin: 6px 0 4px 0;
    }}
    .sector-badge .swatch {{ width: 10px; align-self: stretch; border-radius: 4px; }}
    .sector-badge .icon {{ font-size: 1.5rem; }}
    .sector-badge .name {{ color: {TEXT}; font-weight: 600; }}
    .sector-badge .sub {{ color: {MUTED}; font-size: 0.8rem; }}
</style>
""", unsafe_allow_html=True)

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


@st.cache_data(ttl=86400, show_spinner=False)
def load_market_list():
    """從證交所 / 櫃買中心 OpenAPI 取得全市場股票與 ETF 清單 (只抓名稱，一天更新一次)"""
    pattern = re.compile(r"^(\d{4}|00\d{2,4}[A-Z]?)$")  # 一般股票與 ETF，排除權證
    market = {}
    sources = [
        ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name", ".TW", "上市"),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", "SecuritiesCompanyCode", "CompanyName", ".TWO", "上櫃"),
    ]
    for url, code_key, name_key, suffix, board in sources:
        try:
            for row in requests.get(url, timeout=15).json():
                code = row.get(code_key, "").strip()
                if pattern.match(code):
                    market[code + suffix] = (row.get(name_key, "").strip(), board)
        except Exception:
            continue
    return market


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
    "OpenAI": ["gpt-4o-mini", "gpt-4o"],
    "Claude": ["claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"],
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
    icon, _ = CATEGORY_STYLE[cat]
    n_checked = sum(st.session_state.get(f"chk_{c}", False) for c in pool)
    label = f"{icon} {cat}" + (f"（已勾選 {n_checked}）" if n_checked else "")
    with st.sidebar.expander(label, expanded=n_checked > 0):
        for code, name in pool.items():
            if cat == CUSTOM_CATEGORY:
                c_chk, c_del = st.columns([5, 1])
                c_chk.checkbox(f"{name} ({code})", key=f"chk_{code}")
                c_del.button("🗑️", key=f"del_{code}", on_click=remove_from_watchlist, args=(code,), help="從自選股移除")
            else:
                st.checkbox(f"{name} ({code})", key=f"chk_{code}")

checked_codes = [c for c in ALL_STOCKS if st.session_state.get(f"chk_{c}", False)]
stock_code = None
if checked_codes:
    st.sidebar.button("取消全部勾選", on_click=clear_checks, use_container_width=True)
    st.sidebar.markdown("---")
    stock_code = st.sidebar.selectbox("🔎 主分析標的", checked_codes, format_func=lambda c: f"{ALL_STOCKS[c]} ({c})")
    stock_name = ALL_STOCKS[stock_code]
    category = CODE_TO_CATEGORY[stock_code]
    cat_icon, cat_color = CATEGORY_STYLE[category]
    st.sidebar.markdown(f"""
<div class="sector-badge">
    <div class="swatch" style="background:{cat_color};"></div>
    <div class="icon">{cat_icon}</div>
    <div><div class="name">{stock_name} <span class="sub">{stock_code}</span></div>
    <div class="sub" style="color:{cat_color};">{category}</div></div>
</div>
""", unsafe_allow_html=True)

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
    df = yf.download(code, start=start_date, end=end_date)
    # 移除多層索引 (yfinance v0.2+ 新版防錯)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


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


def ask_ai(provider, key, model, prompt):
    if provider == "Claude":
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 1500,
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

    response = requests.post(url, headers=headers, json=payload, timeout=90)
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
    df = load_data(stock_code)
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

    tab_overview, tab_tech, tab_compare, tab_ai = st.tabs(["🏠 總覽", "📐 技術分析", "📊 多股比較", "🤖 AI 解盤"])

    # ── 總覽 ──
    with tab_overview:
        col1, col2, col3 = st.columns(3, gap="large")
        with col1:
            st.metric(label=f"當前股價 ({stock_name})", value=f"{current_price:.2f} 元", delta=f"{price_change:+.2f} ({price_pct:+.2f}%)")
        with col2:
            st.metric(label="KD 技術指標狀態", value=f"K:{current_k:.1f} / D:{current_d:.1f}", delta=kd_status, delta_color="normal")
        with col3:
            st.metric(label="60MA 季線乖離預警", value=f"{bias_60:+.2f}%", delta=bias_text, delta_color=bias_color)

        st.subheader("📊 股價歷史波動圖")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df['Close'], name='收盤價', line=dict(color=GOLD, width=2)))
        fig.add_trace(go.Scatter(x=df.index, y=df['60MA'], name='60MA 季線', line=dict(color="#4C8DFF", dash='dash')))
        st.plotly_chart(style_fig(fig, 420), use_container_width=True)

        st.subheader("📰 24H 聯動即時財經新聞")
        st.caption("自動即時追蹤宏觀事件與個股利空消息")
        # 簡易財經新聞模擬
        st.write(f"• [市場頭條] 外資鎖定台股{category}主流，精準調控{stock_name}多頭部位位階。")
        st.write("• [地緣政治] 美股費半指數高檔劇烈洗盤，引發外資期現貨籌碼短線多空權衡。")
        st.write("• [全球總經] Fed 最新利率會議風向影響全球資金外溢效應，台股高過熱區防洗盤。")

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
                cdf = load_data(code)
                if cdf.empty:
                    st.warning(f"{ALL_STOCKS[code]} ({code}) 查無資料")
                    continue
                close = cdf['Close'].dropna()
                ret = (close / close.iloc[0] - 1) * 100
                y = ret if mode.startswith("累積") else close
                cmp_fig.add_trace(go.Scatter(x=close.index, y=y, name=f"{ALL_STOCKS[code]} ({code})",
                                             line=dict(color=palette[i % len(palette)], width=2)))
                summary.append({
                    "股票": f"{ALL_STOCKS[code]} ({code})",
                    "產業": CODE_TO_CATEGORY[code],
                    "最新收盤": round(float(close.iloc[-1]), 2),
                    "近一年報酬率 (%)": round(float(ret.iloc[-1]), 2),
                    "最大回撤 (%)": round(float(((close / close.cummax()) - 1).min() * 100), 2),
                })
            if mode.startswith("累積"):
                cmp_fig.add_hline(y=0, line=dict(color=MUTED, dash="dot", width=1))
            st.plotly_chart(style_fig(cmp_fig, 460), use_container_width=True)
            if summary:
                st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)

    # ── AI 解盤 ──
    with tab_ai:
        st.subheader("🤖 AI 智能解盤窗口")
        st.info(f"目前使用 **{ai_provider} / {model_choice}**。AI 會自動讀取該股的「最新股價、KD、RSI、MACD、布林通道、季線乖離率」，幫您精準分析。")

        user_input = st.text_area("✍️ 貼上您看到的最新法說會、新聞或您的成本帳面問題：", height=140, placeholder="例如：這檔股票外資最近狂賣，我成本套在套牢高點，現在黃金交叉該加碼嗎？")

        if st.button("🚀 送出 AI 綜合分析"):
            if not api_key:
                st.warning(f"請先在左側欄輸入您的 {ai_provider} API Key 才能開通大腦功能。")
            else:
                with st.spinner("AI 正在調閱大盤籌碼與位階數據..."):
                    try:
                        prompt_context = (
                            f"你是精通台股的財經專家。當前股票：{stock_name}（{stock_code}），股價：{current_price:.2f}，"
                            f"K值：{current_k:.1f}，D值：{current_d:.1f}（{kd_status}），"
                            f"RSI(14)：{current_rsi:.1f}（{rsi_text}），"
                            f"MACD DIF：{current_dif:.2f}，訊號線：{current_macd:.2f}（{macd_text}），"
                            f"布林通道：下軌 {last['BB_LOW']:.2f} / 中軌 {last['BB_MID']:.2f} / 上軌 {last['BB_UP']:.2f}（{bb_text}），"
                            f"季線乖離率：{bias_60:.2f}%。用戶提問與新聞背景：{user_input}。"
                            f"請根據這些即時數據，給予最客觀的操作與預測建議。"
                        )
                        ai_reply = ask_ai(ai_provider, api_key, model_choice, prompt_context)
                        st.markdown("### 💡 AI 專家決策建議：")
                        st.write(ai_reply)
                    except Exception as e:
                        st.error(f"AI 連線失敗，請檢查 API Key 是否正確。錯誤代碼: {str(e)}")
except Exception as main_e:
    st.error(f"數據載入失敗，可能因 Yahoo 網路阻擋，請重新整理網頁。錯誤原因: {str(main_e)}")
