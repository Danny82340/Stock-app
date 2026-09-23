"""20 年歷史回測 (逐步前推 walk-forward：每個測試日只用「當天以前」看得到的資料重算預測)

可回測：相似情境統計、技術面、動能、風險燈號、月份效應、台積電 ADR / 美股 / 輝達 / VIX / 漲跌停、
        總經 (CPI、利率、殖利率曲線、M2、實質利率、美元、油價、台幣；依實際公布時間延遲)
無法回測 (沒有長期歷史)：法人、融資、借券、夜盤、新聞、月營收、本益比位階、集保、AI

用法：python backtest.py [--step 5] [--start 2006-01-01] [--split 2016-01-01] [--workers 6]
結果：data/backtest/report.md、data/backtest/samples.csv
"""
import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
import yfinance as yf

import stock_model as M

TRACKED = {
    "2330.TW": "台積電", "3711.TW": "日月光投控", "6223.TWO": "旺矽", "6515.TW": "穎崴",
    "2317.TW": "鴻海", "3231.TW": "緯創", "2382.TW": "廣達",
    "1519.TW": "華城", "2409.TW": "友達", "3481.TW": "群創",
    "0050.TW": "元大台灣50", "0056.TW": "元大高股息", "00878.TW": "國泰永續高股息",
}
HORIZONS = {"1d": ("隔天", 1), "1w": ("1 週", 5), "1m": ("1 個月", 21), "1y": ("1 年", 252)}
OUT_DIR = M.DATA_DIR / "backtest"
US_SYMS = ["TSM", "^SOX", "^GSPC", "NVDA", "^VIX"]
FRED_IDS = ["CPIAUCSL", "FEDFUNDS", "T10Y2Y", "M2SL", "DGS10", "T10YIE"]


def _neutralize_live_data():
    """回測時計分卡不可讀取「現在」的資料 (選擇權 P/C、校準後權重)，避免偷看未來"""
    M.load_put_call = lambda: []
    M.load_model_weights = lambda: {"factors": {}, "scale": 1.0, "meta": {}}
    M.load_horizon_model = lambda: {}  # 回測評估的是未校準的原始計分卡


# ─────────────── 總經：依公布延遲取「當時看得到」的數值 ───────────────
def macro_at(t, fred, yh):
    m = {}

    def upto(s, lag_days):
        return s[s.index <= t - pd.Timedelta(days=lag_days)]

    cpi = upto(fred["CPIAUCSL"], 45)          # CPI 約在次月中旬公布
    if len(cpi) > 16:
        m["cpi_yoy"] = float((cpi.iloc[-1] / cpi.iloc[-13] - 1) * 100)
        m["cpi_yoy_3m_ago"] = float((cpi.iloc[-4] / cpi.iloc[-16] - 1) * 100)
        m["cpi_date"] = cpi.index[-1].strftime("%Y/%m")
    ff = upto(fred["FEDFUNDS"], 30)
    if len(ff) > 7:
        m["fed_funds"], m["fed_6m_chg"] = float(ff.iloc[-1]), float(ff.iloc[-1] - ff.iloc[-7])
    cv = upto(fred["T10Y2Y"], 1)
    if len(cv) > 260:
        m["curve"], m["curve_min_1y"] = float(cv.iloc[-1]), float(cv.iloc[-260:].min())
    m2 = upto(fred["M2SL"], 30)
    if len(m2) > 13:
        m["m2_yoy"] = float((m2.iloc[-1] / m2.iloc[-13] - 1) * 100)
    d10, be = upto(fred["DGS10"], 1), upto(fred["T10YIE"], 1)
    if len(d10) and len(be):
        m["us10y"], m["breakeven"] = float(d10.iloc[-1]), float(be.iloc[-1])
        m["real_rate"] = m["us10y"] - m["breakeven"]
    for sym, key in [("DX-Y.NYB", "dxy"), ("CL=F", "oil"), ("TWD=X", "usdtwd")]:
        s = yh[sym].dropna()
        s = s[s.index <= t]
        if len(s) > 63:
            m[key], m[f"{key}_3m"] = float(s.iloc[-1]), float((s.iloc[-1] / s.iloc[-64] - 1) * 100)
    return m


def season_at(adj):
    """只用過去的月報酬計算本月的歷史平均與上漲機率"""
    monthly = adj.resample("ME").last().pct_change().dropna() * 100
    monthly = monthly.iloc[:-1]  # 排除尚未走完的當月
    this_month = adj.index[-1].month
    same = monthly[monthly.index.month == this_month]
    if len(same) < 3:
        return None
    return float(same.mean()), float((same > 0).mean() * 100)


# ─────────────── 單一股票回測 ───────────────
def backtest_stock(args):
    code, name, hist, us_close, fred, yh, meta, step, start = args
    _neutralize_live_data()
    hist = hist.dropna(subset=["Close"])
    hist = hist[hist["Close"] > 0]
    if len(hist) < 900:
        return []
    adj_full = hist["Adj Close"]
    d_full = M.add_indicators(hist)  # 指標皆為滾動 / 指數平滑計算，第 i 列只用到第 i 列以前的資料
    dates = pd.DatetimeIndex(hist.index).normalize()
    us_chg = us_close.pct_change() * 100
    us_idx = pd.DatetimeIndex(us_close.index).normalize()
    link, tech, is_etf = meta["link"], meta["tech"], meta["is_etf"]
    idx_sym = "^SOX" if tech else "^GSPC"
    close_chg = hist["Close"].pct_change() * 100
    rows = []
    first = max(756, int(dates.searchsorted(pd.Timestamp(start))))
    for i in range(first, len(dates) - 1, step):
        t = dates[i]
        adj, d = adj_full.iloc[:i + 1], d_full.iloc[:i + 1]
        price = float(d["Close"].iloc[-1])
        proj = M.compute_projection(adj, price)
        if proj.empty:
            continue
        stat = {r["期間"]: float(r["上漲機率 (%)"]) for _, r in proj.iterrows()}

        # 隔天因子：今天收盤後、明天開盤前的美股 (美國日期 >= 今天 且 < 下一個交易日)
        nd = {}
        lo, hi = us_idx.searchsorted(t, "left"), us_idx.searchsorted(dates[i + 1], "left")
        window = us_chg.iloc[lo:hi]
        if not window.empty:
            moves = window.sum()
            nd["adr"] = M.pts_adr(moves["TSM"], link)
            nd["us_index"] = M.pts_us_index(moves[idx_sym])
            if tech:
                nd["nvda"] = M.pts_nvda(moves["NVDA"])
            nd["vix"] = M.pts_vix(moves["^VIX"], float(us_close["^VIX"].iloc[hi - 1]))
        if not is_etf:
            c = close_chg.iloc[i]
            nd["limit"] = M.pts_limit(c, hist["Close"].iloc[i] >= hist["High"].iloc[i], hist["Close"].iloc[i] <= hist["Low"].iloc[i])
        nd = {k: (0.0 if np.isnan(v) else v) for k, v in nd.items()}
        nd_points = sum(nd.values())
        nd_p = float(np.clip(stat.get("隔天", 50) + nd_points, 15, 85))

        last = d.iloc[-1]
        inp = {
            "code": code, "name": name, "price": price, "nextday_p": nd_p, "nextday_points": nd_points, "is_etf": is_etf,
            "sigma_1d": float(np.log(adj).diff().iloc[-60:].std()), "stat": stat,
            "rsi": float(last["RSI"]), "k": float(last["K"]), "dif": float(last["DIF"]), "macd": float(last["MACD"]),
            "bb_up": float(last["BB_UP"]), "bb_low": float(last["BB_LOW"]),
            "ma20": float(d["Close"].iloc[-20:].mean()), "ma60": float(last["60MA"]), "ma120": np.nan,
            "ret_12_1": float((adj.iloc[-22] / adj.iloc[-253] - 1) * 100) if len(adj) > 253 else np.nan,
            "risk": M.assess_risk(d, adj), "revenue": None, "profit": None, "gross_trend": np.nan,
            "holders_chg": np.nan, "pe": np.nan, "pe_pct": np.nan, "macro": macro_at(t, fred, yh), "events": [],
        }
        s = season_at(adj)
        if s:
            inp["season_this"] = s
        cards = M.build_horizon_scorecards(inp)

        rec = {"code": code, "name": name, "date": t.date().isoformat(), "price": price}
        for key, (label, days) in HORIZONS.items():
            rec[f"stat_{key}"] = stat.get(label, np.nan)
            rec[f"p_{key}"] = cards[label]["prob"]
            j = i + days
            rec[f"ret_{key}"] = float((adj_full.iloc[j] / adj_full.iloc[i] - 1) * 100) if j < len(adj_full) else np.nan
            prow = proj[proj["期間"] == label]
            if not prow.empty:
                pr = prow.iloc[0]
                for q in ["悲觀 (10%)", "保守 (25%)", "樂觀 (75%)", "極樂觀 (90%)"]:
                    rec[f"{key}_{q[:2]}"] = float(pr[q] / price - 1) * 100
            for fk, fv in cards[label]["raw_points"].items():
                rec[f"c{key}_{fk}"] = fv
        for fk, fv in nd.items():
            rec[f"nd_{fk}"] = fv
        rows.append(rec)
    return rows


# ─────────────── 分析 ───────────────
def hit_summary(df, p_col, r_col):
    d = df.dropna(subset=[p_col, r_col])
    d = d[(d[r_col] != 0)]
    if d.empty:
        return None
    called = d[d[p_col] != 50]
    up = d[r_col] > 0
    hit = ((called[p_col] > 50) == (called[r_col] > 0)).mean()
    strong = d[(d[p_col] >= 55) | (d[p_col] <= 45)]
    strong_hit = ((strong[p_col] > 50) == (strong[r_col] > 0)).mean() if len(strong) else np.nan
    brier = (((d[p_col] / 100) - up.astype(float)) ** 2).mean()
    brier_base = ((up.mean() - up.astype(float)) ** 2).mean()
    long_ret = d.loc[d[p_col] >= 55, r_col].mean()
    short_ret = d.loc[d[p_col] <= 45, r_col].mean()
    return {"n": len(d), "hit": hit, "always_up": up.mean(), "strong_n": len(strong), "strong_share": len(strong) / len(d),
            "strong_hit": strong_hit, "brier": brier, "brier_base": brier_base,
            "ret_when_up": long_ret, "ret_when_down": short_ret, "ret_all": d[r_col].mean()}


def calibrate_weights(df, keys, prefix, ret_col, shrink=40):
    """與每日檢討相同的規則：命中率 → 權重 (0~2)，樣本少時向 1.0 收斂"""
    weights = {}
    for k in keys:
        col = f"{prefix}{k}"
        if col not in df.columns:
            continue
        d = df[(df[col].fillna(0) != 0) & (df[ret_col].fillna(0) != 0)]
        n = len(d)
        acc = (np.sign(d[col]) == np.sign(d[ret_col])).mean() if n else 0.5
        weights[k] = float(np.clip(1 + n / (n + shrink) * (acc - 0.5) * 4, 0, 2))
    return weights


# ─────────────── 依回測校準 1 週 / 1 個月 / 1 年模型 (邏輯迴歸 + 樣本外驗證) ───────────────
FIT_HORIZONS = {"1w": "1 週", "1m": "1 個月", "1y": "1 年"}
MIN_BRIER_GAIN = 0.0005  # 驗證期 Brier 至少要比「歷史上漲比例」低這麼多，才算有預測力


def _logit(p):
    p = np.clip(p / 100, 0.05, 0.95)
    return np.log(p / (1 - p))


def fit_logit(X, y, lam=5.0, iters=30):
    """帶 L2 正則化的邏輯迴歸 (牛頓法)，截距不正則化"""
    Xb = np.c_[np.ones(len(X)), X]
    w = np.zeros(Xb.shape[1])
    reg = lam * np.eye(len(w))
    reg[0, 0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xb @ w))
        H = Xb.T @ (Xb * (p * (1 - p))[:, None]) + reg
        g = Xb.T @ (p - y) + reg @ w
        w -= np.linalg.solve(H, g)
    return w


HORIZON_DAYS = {"1d": 1, "1w": 5, "1m": 21, "1y": 252}
MIN_T = 2.0  # 顯著性門檻 (t ≥ 2 約等於 95% 信心)


def factor_significance(x, ret, days):
    """因子與未來報酬的相關係數與 t 值；樣本期間重疊時 (每 step 天取樣、看 days 天報酬) 有效樣本數打折，避免高估顯著性"""
    x, y = np.asarray(x, float), np.asarray(ret, float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    if len(x) < 30 or np.std(x) == 0:
        return 0.0, 0.0, 0
    lo, hi = np.percentile(y, [1, 99])
    y = np.clip(y, lo, hi)  # 去除極端值影響
    r = float(np.corrcoef(x, y)[0, 1])
    n_eff = len(x) * min(1.0, ARGS.step / days)
    t = r * np.sqrt(max(n_eff - 2, 1) / max(1 - r * r, 1e-9))
    return r, float(t), int(n_eff)


def select_factors(train, key, candidates):
    """只保留與未來報酬「正相關且顯著」的因子 (用訓練期判斷，避免偷看驗證期)"""
    kept, removed = [], {}
    for name, col in candidates:
        r, t, n_eff = factor_significance(train[col].fillna(0 if name != "stat" else 50), train[f"ret_{key}"], HORIZON_DAYS[key])
        if r > 0 and t >= MIN_T:
            kept.append((name, col))
        else:
            removed[name] = {"r": round(r, 4), "t": round(t, 2), "n_eff": n_eff}
    return kept, removed


def horizon_features(df, key, selected):
    cols = []
    for name, col in selected:
        cols.append(_logit(df[col].fillna(50).values) if name == "stat" else df[col].fillna(0).values / 10)
    return (np.column_stack(cols) if cols else np.zeros((len(df), 0))), [n for n, _ in selected]


def fit_horizon_models(df, split):
    models, lines = {}, ["## 8. 依回測校準後的模型（1 週 / 1 個月 / 1 年）", "",
                         f"方法：{split} 以前的資料訓練邏輯迴歸（L2 正則化），{split} 之後驗證；驗證期表現優於「歷史上漲比例」才採用模型，否則該期間改用歷史上漲比例。", "",
                         "| 期間 | 驗證期樣本 | 校準後命中率 | 永遠猜漲 | 校準後 Brier | 歷史上漲比例 Brier | 採用 |", "|---|---|---|---|---|---|---|"]
    for key, label in FIT_HORIZONS.items():
        d = df.dropna(subset=[f"ret_{key}"])
        d = d[d[f"ret_{key}"] != 0]
        train, test = d[d["date"] < split], d[d["date"] >= split]
        if len(train) < 500 or len(test) < 500:
            continue
        candidates = [("stat", f"stat_{key}")] + [(c[len(f"c{key}_"):], c) for c in sorted(d.columns) if c.startswith(f"c{key}_")]
        selected, removed = select_factors(train, key, candidates)
        Xtr, names = horizon_features(train, key, selected)
        Xte, _ = horizon_features(test, key, selected)
        ytr, yte = (train[f"ret_{key}"] > 0).astype(float).values, (test[f"ret_{key}"] > 0).astype(float).values
        base = ytr.mean()
        brier_base = float(((base - yte) ** 2).mean())
        if names:
            w = fit_logit(Xtr, ytr)
            p_te = 1 / (1 + np.exp(-(w[0] + Xte @ w[1:])))
            brier_model = float(((p_te - yte) ** 2).mean())
            hit = float(((p_te > 0.5) == (yte > 0.5)).mean())
        else:  # 沒有任何顯著因子 → 只剩歷史上漲比例
            w, brier_model, hit = np.array([0.0]), brier_base, float(max(yte.mean(), 1 - yte.mean()))
        use_model = bool(names) and brier_model < brier_base - MIN_BRIER_GAIN
        if use_model:  # 通過驗證 → 用全部資料重新估計係數 (因子組合不變)
            Xall, _ = horizon_features(d, key, selected)
            w = fit_logit(Xall, (d[f"ret_{key}"] > 0).astype(float).values)
        else:  # 沒通過驗證 → 所有因子都不採用
            removed.update({n: {"r": None, "t": None, "n_eff": None, "reason": "組合未通過樣本外驗證"} for n in names})
        models[label] = {
            "mode": "model" if use_model else "base_rate",
            "intercept": float(w[0]), "coef": {n: float(c) for n, c in zip(names, w[1:])} if use_model else {},
            "removed": removed,
            "test_n": int(len(test)), "test_hit": hit, "test_always_up": float(yte.mean()),
            "test_brier": brier_model, "test_brier_base": brier_base, "split": split,
        }
        lines.append(f"| {label} | {len(test):,} | {fmt(hit)} | {fmt(yte.mean())} | {brier_model:.4f} | {brier_base:.4f} | "
                     f"{'✅ 模型' if use_model else '⛔ 改用歷史上漲比例'} |")
    lines += ["", f"因子篩選（訓練期，與未來報酬正相關且 t ≥ {MIN_T} 才保留；樣本期間重疊時有效樣本數打折）：", ""]
    for label, mdl in models.items():
        kept = "、".join(f"{k} {v:+.2f}" for k, v in sorted(mdl["coef"].items(), key=lambda kv: -abs(kv[1]))) or "無"
        dropped = "、".join(f"{k}（r={v['r']:+.3f}, t={v['t']:+.1f}）" if v.get("r") is not None else f"{k}（{v['reason']}）"
                            for k, v in mdl["removed"].items()) or "無"
        lines += [f"- **{label}** 保留：{kept}", f"  - 篩除：{dropped}"]

    # 隔天因子 (加分制，實盤權重由每日檢討校準)：一樣用訓練期檢定，篩除不顯著的因子
    nd_cols = [(c[3:], c) for c in sorted(df.columns) if c.startswith("nd_")]
    d1 = df.dropna(subset=["ret_1d"])
    _, nd_removed = select_factors(d1[d1["date"] < split], "1d", nd_cols)
    models["隔天"] = {"mode": "additive", "removed": nd_removed}
    lines.append(f"- **隔天** 篩除：" + ("、".join(f"{k}（r={v['r']:+.3f}, t={v['t']:+.1f}）" for k, v in nd_removed.items()) or "無（全部顯著）"))
    return models, lines


def fmt(x, pct=True, digits=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x * 100:.{digits}f}%" if pct else f"{x:+.{digits}f}"


def build_report(df, split, elapsed):
    L = ["# 📊 20 年歷史回測報告", "",
         f"- 期間：{df['date'].min()} ~ {df['date'].max()}，{df['code'].nunique()} 檔股票，{len(df):,} 個測試點（每 {ARGS.step} 個交易日一次）",
         "- 方法：逐步前推（walk-forward），每個測試點只使用當天以前的資料；總經數據依實際公布時間延遲",
         "- 可回測因子：相似情境統計、技術面、動能、風險燈號、月份效應、ADR / 美股 / 輝達 / VIX / 漲跌停、總經",
         "- 未納入（無長期歷史資料）：法人、融資、借券、夜盤、新聞、月營收、本益比位階、集保、AI",
         f"- 執行時間 {elapsed / 60:.1f} 分鐘", ""]

    L += ["## 1. 各期間整體表現", "",
          "| 期間 | 樣本 | 模型命中率 | 永遠猜漲 | 只用統計 | 有表態比例 | 表態時命中率 | 模型說漲時平均報酬 | 模型說跌時平均報酬 | 全部平均報酬 | Brier（模型 / 基準，越低越好） |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, (label, _) in HORIZONS.items():
        s, st_ = hit_summary(df, f"p_{key}", f"ret_{key}"), hit_summary(df, f"stat_{key}", f"ret_{key}")
        if not s:
            continue
        L.append(f"| {label} | {s['n']:,} | **{fmt(s['hit'])}** | {fmt(s['always_up'])} | {fmt(st_['hit']) if st_ else '—'} | "
                 f"{fmt(s['strong_share'])} | {fmt(s['strong_hit'])} | {s['ret_when_up']:+.2f}% | {s['ret_when_down']:+.2f}% | "
                 f"{s['ret_all']:+.2f}% | {s['brier']:.4f} / {s['brier_base']:.4f} |")
    L += ["", "> 「永遠猜漲」是最重要的對照：台股長期上漲，只要一直猜漲命中率就超過 50%。"
          "模型要有價值，必須在「說漲時的平均報酬」明顯高於「說跌時」，或 Brier 分數低於基準。", ""]

    L += ["## 2. 樣本外測試（避免過度擬合）", "",
          f"用 {split} 以前的資料校準隔天因子權重，再套用到 {split} 之後的資料驗證。", ""]
    train, test = df[df["date"] < split], df[df["date"] >= split]
    nd_keys = ["adr", "us_index", "nvda", "vix", "limit"]
    w = calibrate_weights(train, nd_keys, "nd_", "ret_1d")
    test = test.copy()
    test["p_1d_cal"] = np.clip(test["stat_1d"] + sum(test[f"nd_{k}"].fillna(0) * w.get(k, 1.0) for k in nd_keys
                                                     if f"nd_{k}" in test.columns), 15, 85)
    L += ["| 隔天預測 | 樣本 | 命中率 | 永遠猜漲 | 表態時命中率 | Brier |", "|---|---|---|---|---|---|"]
    for label, part, col in [("訓練期（權重 1.0）", train, "p_1d"), ("驗證期（權重 1.0）", test, "p_1d"),
                             ("驗證期（訓練期校準權重）", test, "p_1d_cal"), ("驗證期（只用統計）", test, "stat_1d")]:
        s = hit_summary(part, col, "ret_1d")
        if s:
            L.append(f"| {label} | {s['n']:,} | {fmt(s['hit'])} | {fmt(s['always_up'])} | {fmt(s['strong_hit'])} | {s['brier']:.4f} |")
    L += ["", "訓練期校準出的隔天因子權重：" + "、".join(f"{M.FACTOR_LABELS.get(k, k)} {v:.2f}" for k, v in w.items()), ""]

    L += ["## 3. 機率校準（模型說 X% 時，實際上漲的比例）", "",
          "| 期間 | ≤40% | 40~45% | 45~55% | 55~60% | 60~70% | ≥70% |", "|---|---|---|---|---|---|---|"]
    bins, names = [0, 40, 45, 55, 60, 70, 101], ["≤40", "40~45", "45~55", "55~60", "60~70", "≥70"]
    for key, (label, _) in HORIZONS.items():
        d = df.dropna(subset=[f"p_{key}", f"ret_{key}"])
        cells = []
        for (lo, hi) in zip(bins[:-1], bins[1:]):
            part = d[(d[f"p_{key}"] >= lo) & (d[f"p_{key}"] < hi)]
            cells.append(f"{(part[f'ret_{key}'] > 0).mean() * 100:.0f}%（{len(part):,}）" if len(part) >= 30 else "—")
        L.append(f"| {label} | " + " | ".join(cells) + " |")
    L += ["", "> 校準良好時，模型說 60~70% 的格子，實際上漲比例也應落在 60~70% 附近；括號內為樣本數。", ""]

    L += ["## 4. 價格區間準確度", "", "| 期間 | 實際落在 25%~75% 區間（理論 50%） | 實際落在 10%~90% 區間（理論 80%） |", "|---|---|---|"]
    for key, (label, _) in HORIZONS.items():
        cols = [f"{key}_悲觀", f"{key}_保守", f"{key}_樂觀", f"{key}_極樂"]
        if not all(c in df.columns for c in cols):
            continue
        d = df.dropna(subset=cols + [f"ret_{key}"])
        r = d[f"ret_{key}"]
        in50 = ((r >= d[f"{key}_保守"]) & (r <= d[f"{key}_樂觀"])).mean()
        in80 = ((r >= d[f"{key}_悲觀"]) & (r <= d[f"{key}_極樂"])).mean()
        L.append(f"| {label} | {fmt(in50)} | {fmt(in80)} |")
    L.append("")

    L += ["## 5. 各因子實際表現", "", "| 期間 | 因子 | 出現次數 | 方向命中率 | 偏多時平均報酬 | 偏空時平均報酬 | 結論 |", "|---|---|---|---|---|---|---|"]
    factor_names = {**M.FACTOR_LABELS, "carry": "隔天訊號延續", "reversal": "短期反轉（RSI）", "bollinger": "布林通道",
                    "trend": "均線趨勢", "macd": "MACD 動能", "season": "月份效應", "risk": "風險燈號",
                    "momentum": "12-1 月動能", "inflation": "通膨", "rates": "利率循環", "real_rate": "實質利率",
                    "curve": "殖利率曲線", "liquidity": "M2 貨幣供給", "dollar": "美元指數", "oil": "油價", "twd": "新台幣"}
    groups = [("1d", "nd_", ["adr", "us_index", "nvda", "vix", "limit"])]
    for key in ["1w", "1m", "1y"]:
        groups.append((key, f"c{key}_", sorted({c[len(f'c{key}_'):] for c in df.columns if c.startswith(f"c{key}_")})))
    for key, prefix, keys in groups:
        for k in keys:
            col = f"{prefix}{k}"
            if col not in df.columns:
                continue
            d = df[(df[col].fillna(0).abs() > 0.05)].dropna(subset=[f"ret_{key}"])
            d = d[d[f"ret_{key}"] != 0]
            if len(d) < 50:
                continue
            acc = (np.sign(d[col]) == np.sign(d[f"ret_{key}"])).mean()
            up_ret = d.loc[d[col] > 0, f"ret_{key}"].mean()
            dn_ret = d.loc[d[col] < 0, f"ret_{key}"].mean()
            spread_ok = (not np.isnan(up_ret) and not np.isnan(dn_ret) and up_ret > dn_ret)
            verdict = "✅ 有效" if acc >= 0.53 and (spread_ok or np.isnan(dn_ret) or np.isnan(up_ret)) else \
                      "❌ 反向 / 無效" if acc < 0.48 else "➖ 效果不明顯"
            L.append(f"| {HORIZONS[key][0]} | {factor_names.get(k, k)} | {len(d):,} | {fmt(acc)} | "
                     f"{'—' if np.isnan(up_ret) else f'{up_ret:+.2f}%'} | {'—' if np.isnan(dn_ret) else f'{dn_ret:+.2f}%'} | {verdict} |")
    L += ["", "> 注意：「方向命中率」會受台股長期上漲影響，偏多因子天生較容易命中；要同時看偏多與偏空時的平均報酬差距。", ""]

    L += ["## 6. 不同年代的表現（1 個月預測）", "", "| 年代 | 樣本 | 命中率 | 永遠猜漲 | 說漲時平均報酬 | 說跌時平均報酬 |", "|---|---|---|---|---|---|"]
    years = pd.to_datetime(df["date"]).dt.year
    for lo, hi in [(2006, 2010), (2011, 2015), (2016, 2020), (2021, 2026)]:
        s = hit_summary(df[(years >= lo) & (years <= hi)], "p_1m", "ret_1m")
        if s:
            L.append(f"| {lo}~{hi} | {s['n']:,} | {fmt(s['hit'])} | {fmt(s['always_up'])} | {s['ret_when_up']:+.2f}% | {s['ret_when_down']:+.2f}% |")
    L.append("")

    L += ["## 7. 各股票表現（1 個月預測）", "", "| 股票 | 樣本 | 命中率 | 永遠猜漲 | 說漲時平均報酬 | 說跌時平均報酬 |", "|---|---|---|---|---|---|"]
    for code, g in df.groupby("code"):
        s = hit_summary(g, "p_1m", "ret_1m")
        if s:
            L.append(f"| {g['name'].iloc[0]}（{code}） | {s['n']:,} | {fmt(s['hit'])} | {fmt(s['always_up'])} | "
                     f"{s['ret_when_up']:+.2f}% | {s['ret_when_down']:+.2f}% |")
    return "\n".join(L)


def main():
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("下載歷史資料 ...", flush=True)
    hist = {c: M._flatten(yf.download(c, start="2000-01-01", auto_adjust=False, progress=False)) for c in TRACKED}
    us_close = yf.download(US_SYMS, start="1999-01-01", auto_adjust=False, progress=False)["Close"].ffill()
    fred = {k: M._fred(k) for k in FRED_IDS}
    yh = yf.download(["DX-Y.NYB", "CL=F", "TWD=X"], start="1999-01-01", auto_adjust=False, progress=False)["Close"]
    meta = {c: {"link": M.tsmc_link_of(c), "tech": M.is_tech_stock(c), "is_etf": c.split(".")[0].startswith("00")} for c in TRACKED}
    jobs = [(c, n, hist[c], us_close, fred, yh, meta[c], ARGS.step, ARGS.start) for c, n in TRACKED.items()]
    print(f"開始回測 {len(jobs)} 檔 ...", flush=True)
    rows = []
    with ProcessPoolExecutor(max_workers=ARGS.workers) as ex:
        for code, res in zip(TRACKED, ex.map(backtest_stock, jobs)):
            print(f"  {code}: {len(res)} 個測試點", flush=True)
            rows.extend(res)
    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "samples.csv", index=False)
    report = build_report(df, ARGS.split, time.time() - t0)
    models, lines = fit_horizon_models(df, ARGS.split)
    report += "\n\n" + "\n".join(lines)
    (M.DATA_DIR / "horizon_model.json").write_text(json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "report.md").write_text(report, encoding="utf-8")
    print(report)


def refit_only():
    """不重跑回測，直接用已存的 samples.csv 重新校準"""
    df = pd.read_csv(OUT_DIR / "samples.csv")
    models, lines = fit_horizon_models(df, ARGS.split)
    (M.DATA_DIR / "horizon_model.json").write_text(json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")
    report = (OUT_DIR / "report.md").read_text(encoding="utf-8").split("\n\n## 8.")[0]
    (OUT_DIR / "report.md").write_text(report + "\n\n" + "\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


parser = argparse.ArgumentParser()
parser.add_argument("--step", type=int, default=5)
parser.add_argument("--start", default="2006-01-01")
parser.add_argument("--split", default="2016-01-01")
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--refit", action="store_true", help="只用已存的 samples.csv 重新校準")
ARGS, _ = parser.parse_known_args()

if __name__ == "__main__":
    refit_only() if ARGS.refit else main()
