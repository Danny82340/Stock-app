"""台股 AI 戰情室：資料抓取與模型計算 (不含 Streamlit 畫面)

app.py 與每日檢討腳本 daily_review.py 共用這個模組，確保兩邊用的是同一套模型。
app.py 載入後會替資料函式加上 st.cache_data 快取。
"""
import io
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import NormalDist
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

TAIPEI = ZoneInfo("Asia/Taipei")
DATA_DIR = Path(os.environ.get("STOCK_DATA_DIR", Path(__file__).parent / "data"))  # CI 測試時可改到暫存資料夾


def _to_float(value):
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return np.nan


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
            for row in requests.get(url, timeout=30).json():
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


def load_company_names():
    """公司簡稱 → 代號 (證交所 / 櫃買中心公司基本資料)，用來把 ETF 成分股名稱對應到代號"""
    names = {}
    sources = [
        ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", ".TW", "公司代號", "公司簡稱"),
        ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", ".TWO", "SecuritiesCompanyCode", "CompanyAbbreviation"),
    ]
    for url, suffix, code_key, name_key in sources:
        try:
            for row in requests.get(url, timeout=30).json():
                names[str(row.get(name_key, "")).strip()] = str(row.get(code_key, "")).strip() + suffix
        except Exception:
            continue
    return names


def lookup_stock_name(code):
    """由代號查中文簡稱 (官方行情 → 公司基本資料)，查不到回傳 None"""
    q = load_official_quotes().get(code)
    if q and q.get("name"):
        return q["name"]
    for name, c in load_company_names().items():
        if c == code:
            return name
    return None


def industry_of(code):
    if code.split('.')[0].startswith("00"):
        return "ETF"
    return load_industry_map().get(code, "其他")


# 資料抓取
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


def load_batch_history(codes, period="6mo"):
    """一次下載多檔股票的收盤價 (首頁一覽用)，並以交易所官方最新收盤校正；codes 需為 tuple 才能快取"""
    try:
        raw = yf.download(list(codes), period=period, auto_adjust=False, progress=False)["Close"]
    except Exception:
        return {}
    if isinstance(raw, pd.Series):
        raw = raw.to_frame(codes[0])
    official = load_official_quotes()
    result = {}
    for code in codes:
        if code not in raw.columns:
            continue
        s = raw[code].dropna()
        q = official.get(code)
        if q and not np.isnan(q["Close"]):
            s.loc[q["Date"]] = q["Close"]  # 補上或覆蓋最新交易日
            s = s.sort_index()
        if len(s) >= 2:
            result[code] = s
    return result


def _flatten(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


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
            pub_dt = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(TAIPEI)
            published, ts = pub_dt.strftime("%m/%d %H:%M"), pub_dt.isoformat()
        except Exception:
            published, ts = "", ""
        news.append({"title": title, "source": source, "date": published, "ts": ts, "link": item.findtext("link", "")})
        if len(news) >= limit:
            break
    return news


def load_etf_holdings(code):
    """MoneyDJ ETF 持股明細：成分股、權重、持股增減 (投信每月公布)"""
    try:
        resp = requests.get("https://www.moneydj.com/ETF/X/Basic/Basic0007a.xdjhtm", params={"etfid": code},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        html = resp.content.decode("utf-8", errors="replace")  # 網頁沒標明編碼，requests 會誤判成 ISO-8859-1
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


# ─────────────── 隔天訊號用的資料 ───────────────
US_OVERNIGHT = {"TSM": "台積電 ADR", "^SOX": "費城半導體", "^IXIC": "那斯達克", "^GSPC": "S&P 500", "NVDA": "輝達",
                "^VIX": "VIX 恐慌指數"}


def load_us_overnight():
    """美股最近一個交易日的漲跌 (台股收盤後才開盤，是隔天台股的重要參考)"""
    try:
        raw = yf.download(list(US_OVERNIGHT) + ["TWD=X"], period="10d", auto_adjust=False, progress=False)["Close"]
    except Exception:
        return {}
    result = {}
    for sym in list(US_OVERNIGHT) + ["TWD=X"]:
        if sym not in raw.columns:
            continue
        s = raw[sym].dropna()
        if len(s) >= 2:
            result[sym] = {"date": s.index[-1].date(), "close": float(s.iloc[-1]),
                           "chg": float((s.iloc[-1] / s.iloc[-2] - 1) * 100)}
    return result


def load_taifex_night():
    """期交所台指期 (TX) 近月：日盤結算價與夜盤最新價"""
    try:
        rows = requests.get("https://openapi.taifex.com.tw/v1/DailyMarketReportFut", timeout=20).json()
    except Exception:
        return {}
    tx = [r for r in rows if r.get("Contract") == "TX" and len(str(r.get("ContractMonth(Week)", "")).strip()) == 6]
    if not tx:
        return {}
    near = min(r["ContractMonth(Week)"] for r in tx)
    day = next((r for r in tx if r["ContractMonth(Week)"] == near and r.get("TradingSession") == "一般"), None)
    night = next((r for r in tx if r["ContractMonth(Week)"] == near and r.get("TradingSession") == "盤後"), None)
    if not day or not night:
        return {}
    base = _to_float(day.get("SettlementPrice"))
    if np.isnan(base):
        base = _to_float(day.get("Last"))
    last = _to_float(night.get("Last"))
    if np.isnan(base) or np.isnan(last):
        return {}
    return {"date": day.get("Date"), "day_settle": base, "night_last": last, "night_pct": (last / base - 1) * 100}


def load_taifex_foreign_oi():
    """外資台指期淨未平倉口數 (負數 = 淨空單)"""
    try:
        rows = requests.get("https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate",
                            timeout=20).json()
        row = next(r for r in rows if r.get("ContractCode") == "臺股期貨" and "外資" in r.get("Item", ""))
        return {"date": row.get("Date"), "net_oi": _to_float(row.get("OpenInterest(Net)"))}
    except Exception:
        return {}


def load_t86(date_str):
    """證交所三大法人買賣超日報 (上市，單日全部股票)：代號 → (外資, 投信, 自營商) 買賣超股數"""
    # 失敗時直接拋出例外 (不會被快取)，由呼叫端處理；例如當天資料尚未公布
    time.sleep(0.8)  # 證交所有流量限制
    j = requests.get("https://www.twse.com.tw/rwd/zh/fund/T86",
                     params={"date": date_str, "selectType": "ALLBUT0999", "response": "json"}, timeout=20).json()
    f = j["fields"]
    i_code, i_fi, i_it, i_dl = f.index("證券代號"), f.index("外陸資買賣超股數(不含外資自營商)"), f.index("投信買賣超股數"), f.index("自營商買賣超股數")
    return {r[i_code].strip(): (_to_float(r[i_fi]), _to_float(r[i_it]), _to_float(r[i_dl])) for r in j.get("data", [])}


def load_tpex_insti():
    """櫃買中心三大法人買賣超 (上櫃，最新一日)"""
    try:
        rows = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading", timeout=20).json()
    except Exception:
        return {}
    result = {}
    for r in rows:
        fi = next((v for k, v in r.items() if "Foreign" in k and "Difference" in k and "Dealers" not in k), None)
        result[r.get("SecuritiesCompanyCode", "").strip()] = (
            _to_float(fi), _to_float(r.get("SecuritiesInvestmentTrustCompanies-Difference")), _to_float(r.get("Dealers-Difference")))
    return result


def load_institutional(code, trade_dates):
    """個股近幾日三大法人買賣超：[(日期, 外資, 投信, 自營商)]，由舊到新"""
    stock_no = code.split('.')[0]
    if code.endswith(".TWO"):
        v = load_tpex_insti().get(stock_no)
        return [(trade_dates[-1], *v)] if v else []
    rows = []
    for d in trade_dates[-5:]:
        try:
            v = load_t86(d.strftime("%Y%m%d")).get(stock_no)
        except Exception:
            continue
        if v:
            rows.append((d, *v))
    return rows


def load_margin():
    """融資餘額 (最新一日)：代號 → (前日餘額, 今日餘額)，單位：張"""
    result = {}
    try:
        for r in requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN", timeout=20).json():
            result[r.get("股票代號", "").strip() + ".TW"] = (_to_float(r.get("融資前日餘額")), _to_float(r.get("融資今日餘額")))
    except Exception:
        pass
    try:
        for r in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance", timeout=20).json():
            result[r.get("SecuritiesCompanyCode", "").strip() + ".TWO"] = (
                _to_float(r.get("MarginPurchaseBalancePreviousDay")), _to_float(r.get("MarginPurchaseBalance")))
    except Exception:
        pass
    return result


POSITIVE_WORDS = ["大漲", "上漲", "勁揚", "創高", "新高", "利多", "看好", "調升", "上修", "買超", "強勢", "反彈",
                  "飆", "漲停", "樂觀", "降息", "報喜", "優於預期", "爆單", "滿載"]
NEGATIVE_WORDS = ["大跌", "下跌", "重挫", "暴跌", "利空", "下修", "調降", "賣超", "衰退", "崩", "跌停", "悲觀",
                  "升息", "制裁", "戰爭", "衝突", "砍單", "低於預期", "示警", "恐慌"]


def news_sentiment(items):
    """新聞標題關鍵字情緒：正面詞 +1、負面詞 -1"""
    score = 0
    for n in items:
        score += sum(w in n["title"] for w in POSITIVE_WORDS) - sum(w in n["title"] for w in NEGATIVE_WORDS)
    return score


MARKET_INDICES = {
    "^TWII": "台股加權指數",
    "^SOX": "費城半導體指數",
    "^IXIC": "那斯達克指數",
    "^GSPC": "S&P 500",
    "^VIX": "VIX 恐慌指數",
    "^TNX": "美 10 年債殖利率",
    "TWD=X": "美元兌台幣",
}


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


# ─────────────── 基本面 / 籌碼 / 事件 ───────────────
def load_monthly_revenue():
    """月營收 (上市 + 上櫃)：代號 → 資料年月、當月營收、月增率、年增率、累計年增率、備註"""
    result = {}
    sources = [("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", ".TW"),
               ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O", ".TWO")]
    for url, suffix in sources:
        try:
            for r in requests.get(url, timeout=30).json():
                ym = str(r.get("資料年月", ""))
                result[str(r.get("公司代號", "")).strip() + suffix] = {
                    "month": f"{int(ym[:-2]) + 1911}/{ym[-2:]}" if len(ym) >= 4 else ym,
                    "published": str(r.get("出表日期", "")),
                    "revenue": _to_float(r.get("營業收入-當月營收")),
                    "mom": _to_float(r.get("營業收入-上月比較增減(%)")),
                    "yoy": _to_float(r.get("營業收入-去年同月增減(%)")),
                    "cum_yoy": _to_float(r.get("累計營業收入-前期比較增減(%)")),
                    "note": str(r.get("備註", "")).strip(),
                }
        except Exception:
            continue
    return result


def load_profitability():
    """最新一季營益分析 (上市)：毛利率、營業利益率、稅後純益率"""
    result = {}
    try:
        for r in requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap17_L", timeout=30).json():
            vals = {k: v for k, v in r.items()}
            pick = lambda word: next((_to_float(v) for k, v in vals.items() if k.startswith(word)), np.nan)
            result[str(r.get("公司代號", "")).strip() + ".TW"] = {
                "quarter": f"{int(r.get('年度', 0)) + 1911}Q{r.get('季別', '')}",
                "gross": pick("毛利率"), "operating": pick("營業利益率"), "net": pick("稅後純益率"),
            }
    except Exception:
        pass
    return result


def load_profitability_history(code):
    """每日檢討累積的季度營益資料 (data/profitability.csv)，用來看毛利率趨勢"""
    path = DATA_DIR / "profitability.csv"
    if not path.exists():
        return pd.DataFrame()
    hist = pd.read_csv(path, dtype={"code": str})
    return hist[hist["code"] == code].drop_duplicates("quarter").sort_values("quarter")


def load_holders():
    """集保戶股權分散表 (每週)：股票代號 → 千張大戶、400 張以上、50 張以下散戶持股比例與總人數"""
    try:
        resp = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        raw = pd.read_csv(io.StringIO(resp.content.decode("utf-8-sig")), dtype=str)
    except Exception:
        return {}
    raw.columns = ["date", "code", "level", "people", "shares", "pct"]
    raw["code"] = raw["code"].str.strip()
    raw["level"] = raw["level"].astype(int)
    raw["pct"] = raw["pct"].astype(float)
    raw["people"] = pd.to_numeric(raw["people"], errors="coerce")
    result = {}
    for code, g in raw.groupby("code"):
        lv = g.set_index("level")
        result[code] = {
            "date": str(g["date"].iloc[0]),
            "big1000": float(lv["pct"].get(15, np.nan)),
            "big400": float(lv.loc[lv.index.isin([12, 13, 14, 15]), "pct"].sum()),
            "retail": float(lv.loc[lv.index.isin(range(1, 9)), "pct"].sum()),
            "people": float(lv["people"].get(17, np.nan)),
        }
    return result


def load_holders_history(stock_no):
    """每日檢討每週存下的集保快照 (data/holders/*.csv)"""
    folder = DATA_DIR / "holders"
    if not folder.exists():
        return pd.DataFrame()
    rows = []
    for f in sorted(folder.glob("*.csv")):
        snap = pd.read_csv(f, dtype={"code": str})
        hit = snap[snap["code"] == stock_no]
        if not hit.empty:
            rows.append(hit.iloc[0].to_dict())
    return pd.DataFrame(rows)


def load_sbl(date_str):
    """證交所借券賣出餘額 (上市)：股票代號 → (前日餘額, 當日賣出, 當日餘額) 股數；失敗會拋出例外 (不快取)"""
    time.sleep(0.8)
    j = requests.get("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U",
                     params={"date": date_str, "response": "json"}, timeout=20).json()
    # 欄位前半是融券、後半 (第 8 欄起) 是借券賣出
    return {r[0].strip(): (_to_float(r[8]), _to_float(r[9]), _to_float(r[12])) for r in j.get("data", []) if len(r) > 12}


def load_put_call():
    """期交所台指選擇權 Put/Call 比 (近幾日)"""
    try:
        rows = requests.get("https://openapi.taifex.com.tw/v1/PutCallRatio", timeout=20).json()
    except Exception:
        return []
    return [{"date": r.get("Date"), "oi_ratio": _to_float(r.get("PutCallOIRatio%")),
             "vol_ratio": _to_float(r.get("PutCallVolumeRatio%"))} for r in rows]


def load_dividend_calendar():
    """證交所除權除息預告：代號 → [(日期, 權/息, 現金股利)]"""
    result = {}
    try:
        for r in requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL", timeout=20).json():
            roc = str(r.get("Date", ""))
            if len(roc) < 7:
                continue
            d = datetime(int(roc[:-4]) + 1911, int(roc[-4:-2]), int(roc[-2:])).date()
            result.setdefault(str(r.get("Code", "")).strip() + ".TW", []).append(
                (d, str(r.get("Exdividend", "")).strip(), _to_float(r.get("CashDividend"))))
    except Exception:
        pass
    return result


US_EARNINGS_WATCH = {"NVDA": "輝達", "AAPL": "蘋果", "AMD": "超微", "AVGO": "博通", "MSFT": "微軟",
                     "GOOGL": "Google", "META": "Meta", "AMZN": "亞馬遜", "TSM": "台積電 ADR"}


def load_us_earnings():
    """美國重要客戶 / 同業的下一次財報日"""
    result = {}
    for sym, name in US_EARNINGS_WATCH.items():
        try:
            cal = yf.Ticker(sym).calendar
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if dates:
                result[sym] = (name, pd.Timestamp(dates[0]).date())
        except Exception:
            continue
    return result


# 美國聯準會 2026 年利率決策日 (會議第二天，台灣時間隔天凌晨公布)
FOMC_DECISION_DAYS = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
                      "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]


def upcoming_events(code, today, days=14):
    """未來 N 天內會影響股價的事件：除權息、FOMC、美國財報、月營收公布、台指期結算"""
    end = today + timedelta(days=days)
    events = []
    for d, kind, cash in load_dividend_calendar().get(code, []):
        if today <= d <= end:
            events.append((d, f"除{kind}日" + (f"（現金股利 {cash:.2f} 元，當天股價會扣除）" if cash and not np.isnan(cash) else "")))
    for s in FOMC_DECISION_DAYS:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        if today <= d <= end:
            events.append((d, "美國 FOMC 利率決策（台灣時間隔天凌晨公布，隔天台股波動可能放大）"))
    if industry_of(code) in SEMI_TECH_INDUSTRIES or code in TSMC_HEAVY_ETFS:
        for sym, (name, d) in load_us_earnings().items():
            if today <= d <= end:
                events.append((d, f"{name} 財報（台灣時間隔天清晨，影響 AI / 半導體供應鏈）"))
    # 月營收：每月 10 日前公布
    tenth = today.replace(day=10) if today.day <= 10 else (today.replace(day=1) + timedelta(days=32)).replace(day=10)
    if tenth <= end:
        events.append((tenth, "月營收公布截止日（多數公司在 1~10 日公布上月營收）"))
    # 台指期結算：每月第三個星期三
    for month_start in [today.replace(day=1), (today.replace(day=1) + timedelta(days=32)).replace(day=1)]:
        first_wed = month_start + timedelta(days=(2 - month_start.weekday()) % 7)
        settle = first_wed + timedelta(days=14)
        if today <= settle <= end:
            events.append((settle, "台指期 / 選擇權結算日（結算前後大盤波動常放大）"))
    return sorted(events)


# ─────────────── 總體經濟 (FRED 免金鑰 CSV + Yahoo) ───────────────
def _fred(series_id):
    txt = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id}, timeout=30).text
    s = pd.read_csv(io.StringIO(txt))
    s.columns = ["date", "value"]
    s["value"] = pd.to_numeric(s["value"], errors="coerce")
    return s.dropna().set_index(pd.to_datetime(s.dropna()["date"]))["value"]


def load_macro():
    """總經數據：美國 CPI 通膨、聯邦基金利率、殖利率曲線、M2、10 年債與預期通膨、美元指數、油價、台幣"""
    m = {}
    try:
        cpi = _fred("CPIAUCSL")
        m["cpi_yoy"] = float((cpi.iloc[-1] / cpi.iloc[-13] - 1) * 100)
        m["cpi_yoy_3m_ago"] = float((cpi.iloc[-4] / cpi.iloc[-16] - 1) * 100)
        m["cpi_date"] = cpi.index[-1].strftime("%Y/%m")
    except Exception:
        pass
    try:
        ff = _fred("FEDFUNDS")
        m["fed_funds"], m["fed_6m_chg"] = float(ff.iloc[-1]), float(ff.iloc[-1] - ff.iloc[-7])
    except Exception:
        pass
    try:
        curve = _fred("T10Y2Y")
        m["curve"], m["curve_min_1y"] = float(curve.iloc[-1]), float(curve.iloc[-260:].min())
    except Exception:
        pass
    try:
        m2 = _fred("M2SL")
        m["m2_yoy"] = float((m2.iloc[-1] / m2.iloc[-13] - 1) * 100)
    except Exception:
        pass
    try:
        m["us10y"] = float(_fred("DGS10").iloc[-1])
        m["breakeven"] = float(_fred("T10YIE").iloc[-1])
        m["real_rate"] = m["us10y"] - m["breakeven"]
    except Exception:
        pass
    try:
        raw = yf.download(["DX-Y.NYB", "CL=F", "TWD=X"], period="6mo", auto_adjust=False, progress=False)["Close"]
        for sym, key in [("DX-Y.NYB", "dxy"), ("CL=F", "oil"), ("TWD=X", "usdtwd")]:
            s = raw[sym].dropna()
            if len(s) > 63:
                m[key] = float(s.iloc[-1])
                m[f"{key}_3m"] = float((s.iloc[-1] / s.iloc[-64] - 1) * 100)
    except Exception:
        pass
    return m


def analyze_etf_constituents(etf_code, top_n=10):
    """ETF 前 N 大成分股：權重、持股增減、近一月漲跌、風險燈號；回傳 (全部持股, 資料日期, 前 N 大, 錯誤紀錄)"""
    holdings, date = load_etf_holdings(etf_code)
    if holdings.empty:
        return holdings, date, pd.DataFrame(), ["抓不到持股明細"]
    name_to_code = {**load_company_names(), **{n: c for c, (n, _) in load_market_list().items()}}
    rows, errors = [], []
    for _, h in holdings.head(top_n).iterrows():
        name = str(h["成分股"]).strip()
        code = name_to_code.get(name)
        row = {"成分股": name, "代號": code or "—", "權重 (%)": h["權重 (%)"], "持股增減": h["持股增減"],
               "近一月 (%)": np.nan, "燈號": "—", "主要警訊": ""}
        if not code:
            errors.append(f"{name}：找不到對應代號")
        else:
            try:
                hd = add_indicators(get_price_data(code))
                row["近一月 (%)"] = round(float((hd["Close"].iloc[-1] / hd["Close"].iloc[-22] - 1) * 100), 1)
                hr = assess_risk(hd, load_long_history(code))
                row["燈號"] = f"{hr['emoji']} {hr['level']}"
                row["主要警訊"] = "、".join(s for _, _, s in hr["items"][:2]) or "無"
            except Exception as e:
                errors.append(f"{name}（{code}）：{type(e).__name__}: {e}")
        rows.append(row)
    return holdings, date, pd.DataFrame(rows), errors


# ─────────────── 模型權重 (每日檢討自動校準) ───────────────
FACTOR_LABELS = {
    "adr": "台積電 ADR", "night": "台指期夜盤", "us_index": "美股指數", "nvda": "輝達", "vix": "VIX",
    "limit": "漲跌停", "foreign": "外資", "trust": "投信", "margin": "融資", "fut_oi": "外資台指期",
    "news_market": "夜間市場新聞", "news_stock": "夜間個股新聞", "revenue": "月營收", "holders": "集保大戶",
    "sbl": "借券賣出", "put_call": "Put/Call 比",
}


def load_horizon_model():
    """20 年回測校準的 1 週 / 1 個月 / 1 年模型 (backtest.py 產生 data/horizon_model.json)"""
    try:
        return json.loads((DATA_DIR / "horizon_model.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_model_weights():
    """讀取每日檢討校準後的權重 (data/weights.json)；沒有檔案時全部用預設 1.0"""
    try:
        w = json.loads((DATA_DIR / "weights.json").read_text(encoding="utf-8"))
    except Exception:
        w = {}
    return {"factors": w.get("factors", {}), "scale": float(w.get("scale", 1.0)), "meta": w}


# 因子公式 (app 與每日檢討 / 歷史回填共用，確保算法一致)
def pts_adr(chg, link):
    return float(np.clip(chg * 4 * link, -15, 15))


def pts_night(pct, w):
    return float(np.clip(pct * 4 * w, -12, 12))


def pts_us_index(chg):
    return float(np.clip(chg * 1.5, -6, 6))


def pts_nvda(chg):
    return float(np.clip(chg * 0.8, -4, 4))


def pts_vix(chg, level):
    pts = -3.0 if chg > 10 else 2.0 if chg < -10 else 0.0
    return pts - 2.0 if level > 30 else pts


def pts_limit(chg, locked_up, locked_dn):
    if chg >= 9.5:
        return 8.0 if locked_up else 3.0
    if chg <= -9.5:
        return -8.0 if locked_dn else -3.0
    return 0.0


def tsmc_link_of(code):
    industry = industry_of(code)
    return 1.0 if code == "2330.TW" else TSMC_HEAVY_ETFS.get(code, 0.4 if industry == "半導體業" else 0.2)


def is_tech_stock(code):
    return industry_of(code) in SEMI_TECH_INDUSTRIES or code in TSMC_HEAVY_ETFS


SEMI_TECH_INDUSTRIES = {"半導體業", "電腦及週邊設備業", "電子零組件業", "光電業", "其他電子業", "通信網路業",
                        "電子通路業", "資訊服務業"}
TSMC_HEAVY_ETFS = {"0050.TW": 0.6, "006208.TW": 0.6}


def build_nextday_signals(code, name, d, stat_p):
    """隔天多因子訊號：每個因子換算成「上漲機率」的加減分 (百分點)。
    權重為經驗設定 (ADR、夜盤影響最大)，尚未經回測校準。"""
    signals = []  # (因子, 數據, 解讀, 加減分)
    tw_last = d.index[-1].date()
    tw_close_time = datetime(tw_last.year, tw_last.month, tw_last.day, 13, 30, tzinfo=TAIPEI)
    industry = industry_of(code)
    is_etf = code.split('.')[0].startswith("00")
    is_tech = industry in SEMI_TECH_INDUSTRIES or code in TSMC_HEAVY_ETFS
    tsmc_link = 1.0 if code == "2330.TW" else TSMC_HEAVY_ETFS.get(code, 0.4 if industry == "半導體業" else 0.2)
    us = load_us_overnight()

    def us_after_close(sym):
        return sym in us and us[sym]["date"] >= tw_last

    def verdict(pts):
        return "偏多" if pts > 0.5 else "偏空" if pts < -0.5 else "中性"

    # 1. 台積電 ADR (台股收盤後的美股交易)
    if "TSM" in us:
        a = us["TSM"]
        data = f"{a['date']:%m/%d} {a['chg']:+.2f}%"
        if code == "2330.TW" and "TWD=X" in us:
            premium = (a["close"] * us["TWD=X"]["close"] / 5 / float(d['Close'].iloc[-1]) - 1) * 100
            data += f"，ADR 溢價 {premium:+.1f}%"
        if us_after_close("TSM"):
            pts = pts_adr(a["chg"], tsmc_link)
            signals.append(("adr", "🌙 台積電 ADR", data, f"台股收盤後 ADR {'上漲' if a['chg'] > 0 else '下跌'}，連動度 {tsmc_link:.0%}", pts))
        else:
            signals.append(("adr", "🌙 台積電 ADR", data, "尚無台股收盤後的美股交易", 0.0))

    # 2. 台指期夜盤
    night = load_taifex_night()
    if night:
        w = 1.0 if is_etf else 0.5 if code == "2330.TW" else 0.6
        fresh = night["date"] == tw_last.strftime("%Y%m%d")
        pts = pts_night(night["night_pct"], w) if fresh else 0.0
        signals.append(("night", "🌙 台指期夜盤", f"{night['night_last']:.0f}（較日盤結算 {night['night_pct']:+.2f}%）",
                        "夜盤反映台股收盤後的國際行情" if fresh else "夜盤資料不是最新交易日", pts))

    # 3. 美股指數 / 輝達
    idx_sym = "^SOX" if is_tech else "^GSPC"
    if idx_sym in us:
        s = us[idx_sym]
        pts = pts_us_index(s["chg"]) if us_after_close(idx_sym) else 0.0
        signals.append(("us_index", f"🇺🇸 {US_OVERNIGHT[idx_sym]}", f"{s['date']:%m/%d} {s['chg']:+.2f}%",
                        "科技 / 半導體股連動美股費半" if is_tech else "一般股參考 S&P 500", pts))
    if is_tech and "NVDA" in us:
        s = us["NVDA"]
        pts = pts_nvda(s["chg"]) if us_after_close("NVDA") else 0.0
        signals.append(("nvda", "🇺🇸 輝達", f"{s['date']:%m/%d} {s['chg']:+.2f}%", "AI 供應鏈風向指標", pts))
    if "^VIX" in us:
        v = us["^VIX"]
        pts = pts_vix(v["chg"], v["close"])
        signals.append(("vix", "😨 VIX 恐慌指數", f"{v['close']:.1f}（{v['chg']:+.1f}%）",
                        "恐慌升溫" if pts < 0 else "恐慌降溫" if pts > 0 else "情緒平穩", pts))

    # 4. 漲跌停
    if not is_etf and len(d) >= 2:
        chg = (float(d['Close'].iloc[-1]) / float(d['Close'].iloc[-2]) - 1) * 100
        locked_up = chg >= 9.5 and float(d['Close'].iloc[-1]) >= float(d['High'].iloc[-1])
        locked_dn = chg <= -9.5 and float(d['Close'].iloc[-1]) <= float(d['Low'].iloc[-1])
        if chg >= 9.5:
            signals.append(("limit", "🔒 漲停", f"今日 {chg:+.1f}%", "鎖死漲停，隔天開高機率高" if locked_up else "觸及漲停但打開，追價力道減弱",
                            pts_limit(chg, locked_up, locked_dn)))
        elif chg <= -9.5:
            signals.append(("limit", "🔒 跌停", f"今日 {chg:+.1f}%", "鎖死跌停，賣壓延續機率高" if locked_dn else "觸及跌停後打開",
                            pts_limit(chg, locked_up, locked_dn)))

    # 5. 三大法人
    insti = load_institutional(code, list(d.index[-5:]))
    vol = float(d['Volume'].iloc[-1]) if 'Volume' in d.columns else np.nan
    if insti and vol > 0:
        _, fi, it, _dl = insti[-1]
        fr, tr = fi / vol, it / vol
        pts = 4.0 if fr > 0.10 else 2.0 if fr > 0.03 else -4.0 if fr < -0.10 else -2.0 if fr < -0.03 else 0.0
        streak = 0
        for row in reversed(insti):
            if np.sign(row[1]) == np.sign(fi) and row[1] != 0:
                streak += 1
            else:
                break
        if streak >= 3:
            pts += 2.0 * np.sign(fi)
        signals.append(("foreign", "💰 外資", f"{fi / 1000:+,.0f} 張（占成交量 {fr * 100:+.1f}%）" + (f"，連續 {streak} 天{'買' if fi > 0 else '賣'}超" if streak >= 2 else ""),
                        "外資大買" if pts >= 2 else "外資大賣" if pts <= -2 else "外資動向不明顯", pts))
        pts = 2.0 if tr > 0.02 else -2.0 if tr < -0.02 else 0.0
        signals.append(("trust", "🏦 投信", f"{it / 1000:+,.0f} 張（占成交量 {tr * 100:+.1f}%）",
                        "投信作多" if pts > 0 else "投信調節" if pts < 0 else "投信動向不明顯", pts))

    # 6. 融資 (散戶情緒)
    m = load_margin().get(code)
    if m and m[0] and m[0] > 0 and len(d) >= 2:
        mchg = (m[1] / m[0] - 1) * 100
        up_today = d['Close'].iloc[-1] > d['Close'].iloc[-2]
        pts = -2.0 if (mchg > 3 and up_today) else 1.0 if (mchg < -3 and not up_today) else 0.0
        signals.append(("margin", "📊 融資", f"餘額 {m[1]:,.0f} 張（{mchg:+.1f}%）",
                        "散戶融資追價，短線過熱" if pts < 0 else "融資退場，籌碼較乾淨" if pts > 0 else "融資變化不大", pts))

    # 7. 外資台指期部位
    oi = load_taifex_foreign_oi()
    if oi and not np.isnan(oi.get("net_oi", np.nan)):
        n = oi["net_oi"]
        pts = -2.0 if n < -40000 else 2.0 if n > 10000 else 0.0
        signals.append(("fut_oi", "📉 外資台指期", f"淨未平倉 {n:+,.0f} 口", "外資大量空單避險" if pts < 0 else "外資偏多布局" if pts > 0 else "外資期貨部位中性", pts))

    # 8. 夜間新聞 (台股收盤後)
    def after_tw_close(items):
        return [n for n in items if n.get("ts") and datetime.fromisoformat(n["ts"]) > tw_close_time]
    night_market = after_tw_close(load_news("台股 OR 美股 OR 台積電 OR 費半 OR 聯準會", limit=15, days=2))
    night_stock = after_tw_close(load_news(name, limit=10, days=2))
    s_m, s_s = news_sentiment(night_market), news_sentiment(night_stock)
    if night_market:
        pts = float(np.clip(s_m, -3, 3))
        signals.append(("news_market", "📰 夜間市場新聞", f"{len(night_market)} 則，情緒分數 {s_m:+d}", "依標題關鍵字判讀", pts))
    if night_stock:
        pts = float(np.clip(s_s * 1.5, -4, 4))
        signals.append(("news_stock", f"📰 夜間 {name} 新聞", f"{len(night_stock)} 則，情緒分數 {s_s:+d}", "依標題關鍵字判讀", pts))

    # 9. 月營收 (剛公布的 3 天內影響較大)
    rev = load_monthly_revenue().get(code)
    if rev and not np.isnan(rev["yoy"]):
        pub = rev["published"]
        fresh = False
        if len(pub) >= 7:
            pub_date = datetime(int(pub[:-4]) + 1911, int(pub[-4:-2]), int(pub[-2:])).date()
            fresh = (tw_last - pub_date).days <= 3
        base = 1.5 if rev["yoy"] > 20 else -1.5 if rev["yoy"] < -10 else 0.0
        pts = base * (2 if fresh else 1)
        signals.append(("revenue", "📈 月營收", f"{rev['month']} 年增 {rev['yoy']:+.1f}%、月增 {rev['mom']:+.1f}%",
                        ("剛公布，" if fresh else "") + ("營收高成長" if base > 0 else "營收衰退" if base < 0 else "營收持平"), pts))

    # 10. 集保千張大戶 (每週)
    stock_no = code.split('.')[0]
    hold_hist = load_holders_history(stock_no)
    now_hold = load_holders().get(stock_no)
    if now_hold:
        prev = hold_hist[hold_hist["date"].astype(str) < now_hold["date"]] if not hold_hist.empty else pd.DataFrame()
        if not prev.empty:
            diff = now_hold["big1000"] - float(prev.iloc[-1]["big1000"])
            pts = 1.5 if diff > 0.3 else -1.5 if diff < -0.3 else 0.0
            signals.append(("holders", "🐋 千張大戶", f"{now_hold['big1000']:.2f}%（週變化 {diff:+.2f} 個百分點）",
                            "大戶加碼" if pts > 0 else "大戶減碼" if pts < 0 else "大戶持股穩定", pts))
        else:
            signals.append(("holders", "🐋 千張大戶", f"{now_hold['big1000']:.2f}%（{now_hold['date']}）",
                            "尚無上週資料可比較（每日檢討會每週存檔）", 0.0))

    # 11. 借券賣出 (上市)
    if code.endswith(".TW") and not is_etf:
        try:
            sbl = load_sbl(tw_last.strftime("%Y%m%d")).get(stock_no)
        except Exception:
            sbl = None
        if sbl and sbl[0] and sbl[0] > 0:
            schg = (sbl[2] / sbl[0] - 1) * 100
            pts = -1.5 if schg > 5 else 1.0 if schg < -5 else 0.0
            signals.append(("sbl", "🔻 借券賣出", f"餘額 {sbl[2] / 1000:,.0f} 張（{schg:+.1f}%）",
                            "借券放空增加" if pts < 0 else "借券回補" if pts > 0 else "借券變化不大", pts))

    # 12. 台指選擇權 Put/Call 比 (台灣慣例：未平倉 P/C 比高 = 賣方看支撐，偏多)
    pc = load_put_call()
    if pc and not np.isnan(pc[0]["oi_ratio"]):
        r = pc[0]["oi_ratio"]
        pts = 1.0 if r > 120 else -1.0 if r < 80 else 0.0
        signals.append(("put_call", "⚖️ Put/Call 比", f"未平倉 {r:.0f}%（{pc[0]['date']}）",
                        "支撐偏強" if pts > 0 else "支撐偏弱" if pts < 0 else "多空均衡", pts))

    # 套用每日檢討校準的權重與機率縮放
    weights = load_model_weights()
    rows, raw_points = [], {}
    total = 0.0
    for key, label, data, text, raw in signals:
        w = float(weights["factors"].get(key, 1.0))
        pts = raw * w
        total += pts
        raw_points[key] = raw_points.get(key, 0.0) + raw
        rows.append({"因子": label, "數據": data, "解讀": text, "影響": verdict(pts), "權重": round(w, 2), "加減分": round(pts, 1)})
    final_p = float(np.clip(50 + weights["scale"] * (stat_p + total - 50), 15, 85))
    return pd.DataFrame(rows), final_p, night_market + night_stock, raw_points


# ─────────────── 多期間量化計分卡 (隔天 / 1 週 / 1 個月 / 1 年) ───────────────
HORIZON_DAYS = {"隔天": 1, "1 週": 5, "1 個月": 21, "1 年": 252}


def collect_model_inputs(code, name, d, adj, projection, nextday_p, nextday_points, pe_pct=np.nan, pe_hist=None):
    """整理計分卡需要的所有數據 (app 與每日檢討共用)"""
    last = d.iloc[-1]
    price = float(last["Close"])
    stock_no = code.split(".")[0]
    inp = {"code": code, "name": name, "price": price, "nextday_p": nextday_p, "nextday_points": nextday_points,
           "is_etf": stock_no.startswith("00")}
    inp["sigma_1d"] = float(np.log(adj).diff().iloc[-60:].std()) if len(adj) > 60 else 0.02
    inp["stat"] = {r["期間"]: float(r["上漲機率 (%)"]) for _, r in projection.iterrows()} if not projection.empty else {}
    # 該股歷史上漲比例 (持有 N 天後上漲的比例)：模型沒有預測力的期間用這個當估計
    inp["base_rates"] = {h: float(((adj.shift(-n) / adj - 1).dropna() > 0).mean())
                         for h, n in HORIZON_DAYS.items() if len(adj) > n + 250}
    # 技術面
    ma20 = float(d["Close"].rolling(20).mean().iloc[-1])
    ma120 = float(d["Close"].rolling(120).mean().iloc[-1]) if len(d) >= 120 else np.nan
    inp.update(rsi=float(last["RSI"]), k=float(last["K"]), dif=float(last["DIF"]), macd=float(last["MACD"]),
               bb_up=float(last["BB_UP"]), bb_low=float(last["BB_LOW"]), ma20=ma20, ma60=float(last["60MA"]), ma120=ma120)
    inp["ret_12_1"] = float((adj.iloc[-22] / adj.iloc[-253] - 1) * 100) if len(adj) > 253 else np.nan
    inp["risk"] = assess_risk(d, adj, pe_pct)
    # 籌碼：近 5 日法人
    insti = load_institutional(code, list(d.index[-5:]))
    vol5 = float(d["Volume"].iloc[-5:].sum()) if "Volume" in d.columns else np.nan
    if insti and vol5 > 0:
        inp["foreign_5d"] = sum(r[1] for r in insti if not np.isnan(r[1])) / vol5 * 100
        inp["trust_5d"] = sum(r[2] for r in insti if not np.isnan(r[2])) / vol5 * 100
    # 基本面
    inp["revenue"] = load_monthly_revenue().get(code)
    inp["profit"] = load_profitability().get(code)
    ph = load_profitability_history(code)
    inp["gross_trend"] = float(inp["profit"]["gross"] - ph.iloc[-2]["gross"]) if inp["profit"] and len(ph) >= 2 else np.nan
    holders_now, hh = load_holders().get(stock_no), load_holders_history(stock_no)
    if holders_now and not hh.empty:
        prev = hh[hh["date"].astype(str) < holders_now["date"]]
        inp["holders_chg"] = float(holders_now["big1000"] - prev.iloc[-1]["big1000"]) if not prev.empty else np.nan
    # 估值：官方本益比 + 預估 EPS → 合理價
    val = load_official_valuation().get(code, {})
    inp["pe"], inp["pe_pct"] = val.get("本益比", np.nan), pe_pct
    inp["dividend_yield"] = val.get("殖利率 (%)", np.nan)
    extras = load_yahoo_extras(code)
    fwd_eps = extras.get("預估 EPS（分析師共識）")
    fair_pe = float(pe_hist.median()) if pe_hist is not None and len(pe_hist) > 20 else inp["pe"]
    if fwd_eps and not np.isnan(fair_pe) and inp["pe"] > 0:
        # Yahoo 的預估 EPS 常是「下一個會計年度」，可能跨兩年；未來一年成長率設上下限，避免把多年成長一次算進去
        eps_ttm = price / inp["pe"]
        inp["eps_growth"] = float(np.clip(fwd_eps / eps_ttm - 1, -0.4, 0.35))
        inp["fair_value"] = eps_ttm * (1 + inp["eps_growth"]) * fair_pe
        inp["fair_gap"] = (inp["fair_value"] / price - 1) * 100
    # 季節性
    seas = load_seasonality(code)
    today = datetime.now(TAIPEI).date()
    for key, month in [("season_this", today.month), ("season_next", today.month % 12 + 1)]:
        if not seas.empty and month in seas.index:
            inp[key] = (float(seas.loc[month, "平均報酬 (%)"]), float(seas.loc[month, "上漲機率 (%)"]))
    inp["macro"] = load_macro()
    inp["events"] = upcoming_events(code, today)
    return inp


def build_horizon_scorecards(inp):
    """每個期間：相似情境統計機率 + 各因子加減分 (百分點) → 上漲機率、方向、預估價、信心。
    只用數據與規則，不含主觀判斷；1 週因子權重由每日檢討依實際命中率校準。"""
    weights = load_model_weights()
    week_w = weights["meta"].get("week_factors", {})
    m = inp["macro"]
    cards = {h: [] for h in HORIZON_DAYS}

    def add(h, key, label, theory, data, pts, text=None):
        w = float(week_w.get(key, 1.0)) if h == "1 週" else 1.0
        raw = float(pts)
        if np.isnan(raw):
            return
        pts = raw * w
        cards[h].append({"key": key, "raw": raw, "因子": label, "理論依據": theory, "數據": data,
                         "判讀": text or ("偏多" if pts > 0.5 else "偏空" if pts < -0.5 else "中性"), "加減分": round(pts, 1)})

    # ── 隔天：沿用隔天訊號模型 ──
    add("隔天", "nextday", "隔天訊號綜合", "美股 / ADR / 夜盤 / 法人 / 融資 / 夜間新聞，每日檢討自動校準權重",
        f"綜合上漲機率 {inp['nextday_p']:.0f}%", inp["nextday_p"] - inp["stat"].get("隔天", 50))

    # ── 1 週 ──
    add("1 週", "carry", "隔天訊號延續", "資訊擴散理論：隔夜資訊多在 1~3 天內反映完畢，對一週影響約減半",
        f"隔天訊號 {inp['nextday_points']:+.1f}", inp["nextday_points"] * 0.5)
    if "foreign_5d" in inp:
        f5, t5 = inp["foreign_5d"], inp["trust_5d"]
        add("1 週", "flows", "法人近 5 日買賣超", "籌碼理論：法人資金大、資訊充分，買賣超具延續性",
            f"外資 {f5:+.1f}%、投信 {t5:+.1f}%（占 5 日成交量）", np.clip(f5 * 0.3 + t5 * 0.5, -4, 4))
    rsi = inp["rsi"]
    add("1 週", "reversal", "短期反轉（RSI）", "短期反轉效應（Jegadeesh 1990）：短線漲跌過度後傾向修正",
        f"RSI {rsi:.0f}", -3 if rsi > 75 else -1.5 if rsi > 70 else 3 if rsi < 25 else 1.5 if rsi < 30 else 0)
    bb = -1.5 if inp["price"] >= inp["bb_up"] else 1.5 if inp["price"] <= inp["bb_low"] else 0
    add("1 週", "bollinger", "布林通道位置", "均值回歸：價格偏離均值過遠後傾向回到中軌",
        f"價格 {inp['price']:.2f}／上軌 {inp['bb_up']:.2f}／下軌 {inp['bb_low']:.2f}", bb)
    pc = load_put_call()
    if pc and not np.isnan(pc[0]["oi_ratio"]):
        r = pc[0]["oi_ratio"]
        add("1 週", "sentiment", "選擇權 Put/Call 比", "逆向情緒指標：賣權未平倉高代表支撐強、市場過度悲觀",
            f"未平倉 P/C {r:.0f}%", 1.5 if r > 120 else -1.5 if r < 80 else 0)

    # ── 1 個月 ──
    p, ma20, ma60 = inp["price"], inp["ma20"], inp["ma60"]
    trend = 4 if p > ma20 > ma60 else -4 if p < ma20 < ma60 else 1 if p > ma60 else -1
    add("1 個月", "trend", "均線趨勢", "趨勢跟隨（道氏理論）：多頭排列時中期上漲機率較高",
        f"價格 {p:.2f}／20MA {ma20:.2f}／60MA {ma60:.2f}", trend,
        "多頭排列" if trend == 4 else "空頭排列" if trend == -4 else "季線之上" if trend > 0 else "季線之下")
    macd_pts = 2 if inp["dif"] > 0 and inp["dif"] > inp["macd"] else -2 if inp["dif"] < 0 and inp["dif"] < inp["macd"] else 0
    add("1 個月", "macd", "MACD 動能", "動能指標：DIF 在零軸之上且高於訊號線代表中期動能偏多",
        f"DIF {inp['dif']:.2f}／訊號線 {inp['macd']:.2f}", macd_pts)
    rev = inp.get("revenue")
    if rev and not np.isnan(rev["yoy"]):
        y = rev["yoy"]
        add("1 個月", "revenue", "月營收年增率", "盈餘動能 / 盈餘宣告後漂移（PEAD）：營收成長的利多通常持續反映數週",
            f"{rev['month']} 年增 {y:+.1f}%", 4 if y > 30 else 2 if y > 10 else -4 if y < -10 else -1 if y < 0 else 0)
    if "season_this" in inp:
        avg, win = inp["season_this"]
        add("1 個月", "season", "月份效應", "季節效應：除權息、財報、作帳行情造成的月份規律",
            f"本月歷史平均 {avg:+.2f}%、上漲機率 {win:.0f}%", np.clip((win - 50) / 5, -3, 3))
    if not np.isnan(inp.get("holders_chg", np.nan)):
        hc = inp["holders_chg"]
        add("1 個月", "holders", "千張大戶週變化", "籌碼集中度：大戶增加、散戶減少通常有利中期走勢",
            f"{hc:+.2f} 個百分點", 2 if hc > 0.3 else -2 if hc < -0.3 else 0)
    risk = inp["risk"]
    add("1 個月", "risk", "風險燈號", "多指標風險：過熱與轉弱訊號越多，中期回檔機率越高",
        f"{risk['emoji']} {risk['level']}（{risk['score']} 分）", float(np.clip(-(risk["score"] - 1), -5, 1)))

    # ── 1 年 ──
    if not np.isnan(inp.get("pe_pct", np.nan)):
        pp = inp["pe_pct"]
        add("1 年", "pe_pct", "本益比歷史位階", "價值投資 / 估值均值回歸：本益比在歷史高檔時，未來長期報酬較低",
            f"近一年 {pp:.0f} 百分位", 6 if pp < 25 else 2 if pp < 50 else -6 if pp > 75 else -2)
    if "fair_gap" in inp:
        g = inp["fair_gap"]
        add("1 年", "fair_value", "合理價缺口", "盈餘折現：預估 EPS × 合理本益比推算合理價，價格終將向價值收斂",
            f"合理價 {inp['fair_value']:.2f}（{g:+.1f}%；未來一年 EPS 成長 {inp['eps_growth'] * 100:+.0f}%，上限 35%）",
            np.clip(g / 3, -8, 8))
    if rev and not np.isnan(rev.get("cum_yoy", np.nan)):
        cy = rev["cum_yoy"]
        add("1 年", "growth", "營收成長（今年累計）", "成長因子：營收與獲利成長是長期股價的核心驅動",
            f"累計年增 {cy:+.1f}%", 5 if cy > 20 else 2 if cy > 5 else -5 if cy < 0 else 0)
    prof = inp.get("profit")
    if prof and not np.isnan(prof["gross"]):
        gpts = 2 if prof["gross"] > 40 else -1 if prof["gross"] < 10 else 0
        gt = inp.get("gross_trend", np.nan)
        if not np.isnan(gt):
            gpts += 1.5 if gt > 1 else -1.5 if gt < -1 else 0
        add("1 年", "quality", "毛利率（品質）", "品質因子（Novy-Marx 2013）：高毛利、毛利率改善的公司長期報酬較佳",
            f"{prof['quarter']} 毛利率 {prof['gross']:.1f}%" + (f"（較上季 {gt:+.1f}）" if not np.isnan(gt) else ""), gpts)
    if not np.isnan(inp.get("ret_12_1", np.nan)):
        mo = inp["ret_12_1"]
        add("1 年", "momentum", "12-1 月動能", "動能效應（Jegadeesh & Titman 1993）：過去 12 個月（排除最近 1 個月）強勢股傾向延續",
            f"{mo:+.1f}%", 3 if mo > 20 else -3 if mo < -20 else 0)

    # ── 總經 (1 年全權重；1 個月半權重) ──
    macro_rows = []
    if "cpi_yoy" in m:
        c = m["cpi_yoy"]
        pts = -3 if c < 0 else 2 if c <= 3 else -1 if c <= 5 else -4
        macro_rows.append(("inflation", "通貨膨脹（美國 CPI）",
                           "通膨理論：溫和通膨（1~3%）帶動名目營收與獲利成長，股票是抗通膨資產；通膨過高引發升息、壓抑估值；通縮代表需求疲弱",
                           f"{m['cpi_date']} 年增 {c:.1f}%（3 個月前 {m['cpi_yoy_3m_ago']:.1f}%）", pts))
    if "fed_6m_chg" in m:
        ch = m["fed_6m_chg"]
        macro_rows.append(("rates", "利率循環（聯邦基金利率）",
                           "股利折現模型：降息使折現率下降、資金成本降低，推升估值；升息則相反",
                           f"{m['fed_funds']:.2f}%（近 6 個月 {ch:+.2f}）", 3 if ch <= -0.25 else -3 if ch >= 0.25 else 0))
    if "real_rate" in m:
        rr = m["real_rate"]
        macro_rows.append(("real_rate", "實質利率",
                           "實質利率 = 10 年債殖利率 − 預期通膨；實質利率越高，成長股與高本益比股票的估值壓力越大",
                           f"{rr:.2f}%（10 年債 {m['us10y']:.2f}% − 預期通膨 {m['breakeven']:.2f}%）", -2 if rr > 2 else 1 if rr < 1 else 0))
    if "curve" in m:
        cv = m["curve"]
        macro_rows.append(("curve", "殖利率曲線（10 年 − 2 年）",
                           "殖利率曲線倒掛是經濟衰退的領先指標；剛解除倒掛時歷史上衰退風險仍高",
                           f"{cv:+.2f}%（一年內最低 {m['curve_min_1y']:+.2f}%）",
                           -3 if cv < 0 else -1 if m["curve_min_1y"] < 0 else 1))
    if "m2_yoy" in m:
        mm = m["m2_yoy"]
        macro_rows.append(("liquidity", "貨幣供給（美國 M2）",
                           "貨幣數量學說 / 流動性理論：資金寬鬆推升資產價格，貨幣緊縮則相反",
                           f"年增 {mm:+.1f}%", 2 if mm > 5 else -2 if mm < 0 else 0))
    if "real_rate" in m and not np.isnan(inp.get("pe", np.nan)) and inp["pe"] > 0:
        erp = 100 / inp["pe"] - m["us10y"]
        macro_rows.append(("erp", "股債風險溢酬（Fed Model）",
                           "股票盈餘殖利率（1 ÷ 本益比）高於公債殖利率越多，股票相對越有吸引力",
                           f"盈餘殖利率 {100 / inp['pe']:.2f}% − 10 年債 {m['us10y']:.2f}% = {erp:+.2f}%", 2 if erp > 2 else -3 if erp < 0 else 0))
    if "dxy_3m" in m:
        dx = m["dxy_3m"]
        macro_rows.append(("dollar", "美元指數",
                           "美元走強時全球資金回流美國，新興市場（含台股）承壓；美元走弱則資金外溢",
                           f"{m['dxy']:.1f}（近 3 個月 {dx:+.1f}%）", -2 if dx > 3 else 2 if dx < -3 else 0))
    if "oil_3m" in m:
        oi = m["oil_3m"]
        macro_rows.append(("oil", "國際油價",
                           "油價大漲推升成本型通膨、壓縮企業利潤，也提高升息風險",
                           f"{m['oil']:.1f} 美元（近 3 個月 {oi:+.1f}%）", -1.5 if oi > 20 else 1 if oi < -20 else 0))
    if "usdtwd_3m" in m:
        tw = m["usdtwd_3m"]
        macro_rows.append(("twd", "新台幣匯率",
                           "台幣升值通常代表外資匯入，有利台股資金面；貶值代表資金外流",
                           f"美元兌台幣 {m['usdtwd']:.2f}（近 3 個月 {tw:+.1f}%）", 1.5 if tw < -2 else -1.5 if tw > 2 else 0))
    for key, label, theory, data, pts in macro_rows:
        add("1 年", key, label, theory, data, pts)
        add("1 個月", key, label, theory + "（中期影響較小，半權重）", data, pts * 0.5)

    # ── 依 20 年回測校準 (data/horizon_model.json)：通過樣本外驗證的期間用邏輯迴歸係數；
    #    沒通過的期間改用該股歷史上漲比例 (模型無預測力時，最誠實的估計) ──
    horizon_model = load_horizon_model()
    base_label = {}
    for h in HORIZON_DAYS:
        hm = horizon_model.get(h)
        if h == "隔天" or not hm:
            continue
        rows = cards[h]
        if hm["mode"] == "base_rate":
            br = inp.get("base_rates", {}).get(h)
            inp["stat"][h] = br * 100 if br is not None else inp["stat"].get(h, 50.0)
            base_label[h] = (f"歷史上漲比例（此期間模型經 20 年回測無預測力，驗證期命中率 {hm['test_hit'] * 100:.1f}% "
                             f"< 永遠猜漲 {hm['test_always_up'] * 100:.1f}%）")
            for r in rows:
                r["加減分"], r["判讀"] = 0.0, "回測無效，不計分"
                r["驗證"] = "⛔ 回測無效"
            continue
        coef = hm["coef"]
        terms = {}
        for r in rows:
            if r["key"] in coef:
                terms[id(r)] = coef[r["key"]] * r["raw"] / 10
                r["驗證"] = "✅ 回測權重"
            else:  # 回測沒有長期資料的因子 (法人、營收…)：換算成邏輯值後半權重
                terms[id(r)] = r["raw"] / 25 * 0.5
                r["驗證"] = "⚠️ 未回測（半權重）"
        z0 = hm["intercept"] + coef.get("stat", 0) * float(np.log(np.clip(inp["stat"].get(h, 50) / 100, 0.05, 0.95)
                                                                  / (1 - np.clip(inp["stat"].get(h, 50) / 100, 0.05, 0.95))))
        z = z0 + sum(terms.values())
        sig = lambda x: 1 / (1 + np.exp(-x))
        for r in rows:  # 每個因子的貢獻 = 有它與沒有它的機率差
            r["加減分"] = round(float((sig(z) - sig(z - terms[id(r)])) * 100), 1)
            r["判讀"] = "偏多" if r["加減分"] > 0.5 else "偏空" if r["加減分"] < -0.5 else "中性"
        inp["stat"][h] = float(sig(z0) * 100)
        base_label[h] = (f"回測校準基礎（相似情境統計經 20 年回測校準；驗證期命中率 {hm['test_hit'] * 100:.1f}%，"
                         f"永遠猜漲 {hm['test_always_up'] * 100:.1f}%）")

    # ── 彙總：機率 → 方向 / 預估價 / 信心 ──
    result = {}
    for h, days in HORIZON_DAYS.items():
        rows = cards[h]
        base = inp["stat"].get(h, 50.0)
        total = sum(r["加減分"] for r in rows)
        prob = float(np.clip(base + total, 5, 95))
        sigma = inp["sigma_1d"] * np.sqrt(days)
        mu = sigma * NormalDist().inv_cdf(prob / 100)
        signed = [r["加減分"] for r in rows if abs(r["加減分"]) > 0.5]
        agree = (sum(1 for x in signed if np.sign(x) == np.sign(prob - 50)) / len(signed)) if signed and prob != 50 else 0.5
        edge = abs(prob - 50)
        confidence = "高" if edge >= 15 and agree >= 0.65 else "中" if edge >= 7 and agree >= 0.5 else "低"
        table = pd.DataFrame([{"因子": "📊 基礎機率", "理論依據": base_label.get(h, "相似情境統計：過去相同乖離位階與趨勢下的實際漲跌機率（已換成合理基準成長）"),
                               "數據": f"上漲機率 {base:.0f}%", "判讀": "基礎機率", "加減分": np.nan}]
                             + [{k: v for k, v in r.items() if k not in ("key", "raw")} for r in rows])
        result[h] = {
            "prob": prob, "base": base, "total": total,
            "direction": "📈 上漲" if prob >= 55 else "📉 下跌" if prob <= 45 else "➡️ 盤整",
            "price": inp["price"] * np.exp(mu), "low": inp["price"] * np.exp(mu - sigma), "high": inp["price"] * np.exp(mu + sigma),
            "confidence": confidence, "agree": agree, "table": table,
            "points": {r["key"]: r["加減分"] for r in rows},
            "raw_points": {r["key"]: r["raw"] for r in rows},
        }
    return result


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
