"""每日檢討與自我校準 (GitHub Actions 於台灣時間週一至週五 07:00、台股開盤前執行)

流程：
1. 驗證：把之前存下的預測 (data/predictions.csv) 和實際漲跌比對
2. 校準：依每個因子的實際命中率調整權重，並校準整體機率 (data/weights.json)
3. 預測：用最新的 ADR / 夜盤 / 法人等資料，產生今天的預測並存檔
4. 檢討報告：data/reviews/YYYY-MM-DD.md；有設定 ANTHROPIC_API_KEY 時加上 AI 檢討
5. 快照：每週存集保大戶、每季存營益分析，供 app 顯示趨勢

用法：python daily_review.py [--smoke] [--backfill]
  --smoke     只對 2 檔股票跑一次預測、不寫任何檔案 (CI 檢查用)
  --backfill  重新計算價格類因子的歷史回填 (預設只有檔案不存在時才做)
"""
import argparse
import functools
import json
import os
from datetime import datetime

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
PRED_FILE = M.DATA_DIR / "predictions.csv"
BACKFILL_FILE = M.DATA_DIR / "factor_backfill.csv"
WEIGHTS_FILE = M.DATA_DIR / "weights.json"
REVIEW_DIR = M.DATA_DIR / "reviews"
CONTEXT_DIR = M.DATA_DIR / "contexts"
FACTOR_KEYS = list(M.FACTOR_LABELS)
# 1 週計分卡中可用實際一週報酬校準的因子 (統計基礎另計)
WEEK_FACTOR_LABELS = {"carry": "隔天訊號延續", "flows": "法人近 5 日買賣超", "reversal": "短期反轉（RSI）",
                      "bollinger": "布林通道位置", "sentiment": "選擇權 Put/Call 比"}
WEEK_FACTOR_KEYS = list(WEEK_FACTOR_LABELS)
SHRINK = 40            # 樣本越少，權重越接近預設 1.0
MIN_SAMPLES_SCALE = 40  # 累積足夠驗證筆數後才校準整體機率
BIG_MOVE = 3.0          # 單日漲跌超過 3% 視為大波動，特別檢討
AI_MODEL = "claude-sonnet-5"
MIN_SELECT_N = 60       # 累積這麼多筆才做顯著性檢定
MIN_T = 2.0             # 與未來報酬正相關且 t ≥ 2 才保留


def significance(raw, ret):
    """相關係數與 t 值 (隔天 / 每日資料不重疊，樣本數不需打折)"""
    x, y = np.asarray(raw, float), np.asarray(ret, float)
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return 0.0, 0.0
    r = float(np.corrcoef(x, y)[0, 1])
    return r, float(r * np.sqrt((len(x) - 2) / max(1 - r * r, 1e-9)))

# 同一次執行內，全市場資料只抓一次 (避免對證交所重複請求被限流；例外不會被快取)
for _name in ["load_official_quotes", "load_industry_map", "load_company_names", "load_data", "load_news", "load_us_overnight",
              "load_taifex_night", "load_taifex_foreign_oi", "load_t86", "load_tpex_insti", "load_margin",
              "load_long_history", "load_monthly_revenue", "load_profitability", "load_holders", "load_sbl",
              "load_put_call", "load_dividend_calendar", "load_us_earnings", "load_macro", "load_etf_holdings",
              "load_seasonality", "load_official_valuation", "load_yahoo_extras"]:
    setattr(M, _name, functools.lru_cache(maxsize=None)(getattr(M, _name)))


def tracked_stocks():
    """預設追蹤清單；可在 data/tracked.json 以 {代號: 名稱} 追加"""
    stocks = dict(TRACKED)
    extra = M.DATA_DIR / "tracked.json"
    if extra.exists():
        stocks.update(json.loads(extra.read_text(encoding="utf-8")))
    return stocks


# ─────────────── 預測 ───────────────
def predict_stock(code, name):
    df = M.add_indicators(M.get_price_data(code))
    adj = M.load_long_history(code)
    if df.empty or len(adj) < 300:
        return None, None
    price = float(df["Close"].iloc[-1])
    proj = M.compute_projection(adj, price)

    def horizon(label):
        r = proj[proj["期間"] == label] if not proj.empty else proj
        return r.iloc[0] if not r.empty else None

    day, week = horizon("隔天"), horizon("1 週")
    stat_p = float(day["上漲機率 (%)"]) if day is not None else 50.0
    table, final_p, news, raw = M.build_nextday_signals(code, name, df, stat_p)
    nextday_points = float(table["加減分"].sum()) if not table.empty else 0.0
    inp = M.collect_model_inputs(code, name, df, adj, proj, final_p, nextday_points)
    cards = M.build_horizon_scorecards(inp)
    rec = {
        "made_at": datetime.now(M.TAIPEI).isoformat(timespec="minutes"),
        "base_date": df.index[-1].date().isoformat(), "code": code, "name": name,
        "base_close": round(price, 2), "stat_p": round(stat_p, 1), "final_p": round(final_p, 1),
        "week_stat_p": round(float(week["上漲機率 (%)"]), 1) if week is not None else np.nan,
        "week_p": round(cards["1 週"]["prob"], 1), "week_price": round(cards["1 週"]["price"], 2),
        "month_p": round(cards["1 個月"]["prob"], 1), "year_p": round(cards["1 年"]["prob"], 1),
    }
    for k in FACTOR_KEYS:
        rec[f"f_{k}"] = round(raw.get(k, 0.0), 2)
    for k in WEEK_FACTOR_KEYS:
        rec[f"w_{k}"] = round(cards["1 週"]["raw_points"].get(k, 0.0), 2)
    context = {"signals": table.to_dict("records"), "news": [n["title"] for n in news[:8]],
               "verdicts": {h: {"prob": round(c["prob"], 1), "direction": c["direction"], "confidence": c["confidence"]}
                            for h, c in cards.items()}}
    return rec, context


# ─────────────── 驗證 ───────────────
def evaluate(preds):
    """用還原除權息價計算實際報酬 (除息日股價下跌不算預測錯誤)"""
    for col in ("target_date", "ret_1d", "ret_1w"):
        if col not in preds.columns:
            preds[col] = np.nan
    preds["target_date"] = preds["target_date"].astype(object)
    histories = {}
    for i, r in preds.iterrows():
        need_1d, need_1w = pd.isna(r["ret_1d"]), pd.isna(r["ret_1w"])
        if not (need_1d or need_1w):
            continue
        if r["code"] not in histories:
            histories[r["code"]] = M.load_long_history(r["code"])
        adj = histories[r["code"]]
        if adj.empty:
            continue
        dates = pd.DatetimeIndex(adj.index).normalize()
        pos = dates.searchsorted(pd.Timestamp(r["base_date"]), side="right") - 1
        if pos < 0:
            continue
        if need_1d and pos + 1 < len(adj):
            preds.at[i, "target_date"] = dates[pos + 1].date().isoformat()
            preds.at[i, "ret_1d"] = round(float(adj.iloc[pos + 1] / adj.iloc[pos] - 1) * 100, 3)
        if need_1w and pos + 5 < len(adj):
            preds.at[i, "ret_1w"] = round(float(adj.iloc[pos + 5] / adj.iloc[pos] - 1) * 100, 3)
    return preds


def hit_stats(df, p_col, ret_col):
    """命中率：機率 > 50 猜漲、< 50 猜跌；另附「永遠猜漲」的基準"""
    if p_col not in df.columns or ret_col not in df.columns:
        return None
    d = df.dropna(subset=[ret_col, p_col])
    d = d[(d[p_col] != 50) & (d[ret_col] != 0)]
    if d.empty:
        return None
    hit = (d[p_col] > 50) == (d[ret_col] > 0)
    conf = d[(d[p_col] >= 60) | (d[p_col] <= 40)]
    conf_hit = ((conf[p_col] > 50) == (conf[ret_col] > 0)).mean() if not conf.empty else np.nan
    return {"n": len(d), "hit": float(hit.mean()), "baseline": float((d[ret_col] > 0).mean()),
            "conf_n": len(conf), "conf_hit": float(conf_hit) if not np.isnan(conf_hit) else None}


# ─────────────── 歷史回填 (價格類因子) ───────────────
def backfill_price_factors(stocks, years=5):
    """用過去 N 年資料回算 ADR / 美股 / 輝達 / VIX / 漲跌停因子與隔天實際報酬，讓權重一開始就有依據"""
    us = yf.download(["TSM", "^SOX", "^GSPC", "NVDA", "^VIX"], period=f"{years}y", auto_adjust=False, progress=False)["Close"]
    us_chg = us.pct_change() * 100
    us_idx = pd.DatetimeIndex(us_chg.index).normalize()
    rows = []
    for code in stocks:
        tw = M._flatten(yf.download(code, period=f"{years}y", auto_adjust=False, progress=False)).dropna(subset=["Close"])
        if len(tw) < 60:
            continue
        ret = tw["Adj Close"].pct_change() * 100
        close_chg = tw["Close"].pct_change() * 100
        link, tech = M.tsmc_link_of(code), M.is_tech_stock(code)
        idx_sym = "^SOX" if tech else "^GSPC"
        is_etf = code.split(".")[0].startswith("00")
        dates = pd.DatetimeIndex(tw.index).normalize()
        for i in range(1, len(dates)):
            r = ret.iloc[i]
            if np.isnan(r) or r == 0:
                continue

            def add(key, pts):
                if pts and not np.isnan(pts):
                    rows.append({"date": dates[i].date().isoformat(), "code": code, "key": key, "raw": round(pts, 2), "ret": round(r, 3)})

            # 前一個台股交易日收盤後、當天開盤前的美股交易 (美國日期 >= 前一交易日 且 < 當天)
            lo, hi = us_idx.searchsorted(dates[i - 1], "left"), us_idx.searchsorted(dates[i], "left")
            window = us_chg.iloc[lo:hi]
            if not window.empty:
                moves = window.sum()
                add("adr", M.pts_adr(moves["TSM"], link))
                add("us_index", M.pts_us_index(moves[idx_sym]))
                if tech:
                    add("nvda", M.pts_nvda(moves["NVDA"]))
                add("vix", M.pts_vix(moves["^VIX"], float(us["^VIX"].iloc[hi - 1])))
            if not is_etf:
                c, h, lw, cl = close_chg.iloc[i - 1], tw["High"].iloc[i - 1], tw["Low"].iloc[i - 1], tw["Close"].iloc[i - 1]
                add("limit", M.pts_limit(c, cl >= h, cl <= lw))
    return pd.DataFrame(rows)


# ─────────────── 校準 ───────────────
def calibrate(preds, backfill, old, today):
    """每個因子：方向命中率越高權重越大 (0~2)；樣本少時向 1.0 收斂。整體機率縮放用 Brier 分數最小化"""
    done = preds.dropna(subset=["ret_1d"]) if not preds.empty else preds
    stats, weights, removed = {}, {}, {}
    for k in FACTOR_KEYS:
        col = f"f_{k}"
        live = done[[col, "ret_1d"]].rename(columns={col: "raw"}) if col in done.columns else pd.DataFrame(columns=["raw", "ret_1d"])
        live = live[(live["raw"] != 0) & (live["ret_1d"] != 0)]
        hist = backfill[backfill["key"] == k][["raw", "ret"]].rename(columns={"ret": "ret_1d"}) if not backfill.empty else pd.DataFrame()
        both = pd.concat([live, hist], ignore_index=True)
        n = len(both)
        if n == 0:
            weights[k] = 1.0
            stats[k] = {"acc": None, "n": 0, "live_n": 0, "weight": 1.0}
            continue
        acc = float((np.sign(both["raw"]) == np.sign(both["ret_1d"])).mean())
        live_acc = float((np.sign(live["raw"]) == np.sign(live["ret_1d"])).mean()) if len(live) else None
        weights[k] = round(float(np.clip(1 + n / (n + SHRINK) * (acc - 0.5) * 4, 0, 2)), 2)
        r, t = significance(both["raw"], both["ret_1d"])
        if n >= MIN_SELECT_N and not (r > 0 and t >= MIN_T):  # 相關性低 / 不顯著 → 篩除
            removed[k] = f"{n} 筆驗證不顯著（r={r:+.3f}, t={t:+.1f}）"
            weights[k] = 0.0
        stats[k] = {"acc": round(acc, 3), "n": n, "live_n": len(live), "r": round(r, 4), "t": round(t, 2),
                    "live_acc": round(live_acc, 3) if live_acc is not None else None, "weight": weights[k]}

    # 1 週因子：用實際 5 個交易日報酬驗證 (沒有歷史回填，純實盤累積)
    week_done = preds.dropna(subset=["ret_1w"]) if "ret_1w" in preds.columns else pd.DataFrame()
    week_stats, week_weights, week_removed = {}, {}, {}
    for k in WEEK_FACTOR_KEYS:
        col = f"w_{k}"
        live = week_done[[col, "ret_1w"]] if col in week_done.columns else pd.DataFrame(columns=[col, "ret_1w"])
        live = live[(live[col] != 0) & (live["ret_1w"] != 0)]
        n = len(live)
        if n == 0:
            week_weights[k], week_stats[k] = 1.0, {"acc": None, "n": 0, "weight": 1.0}
            continue
        acc = float((np.sign(live[col]) == np.sign(live["ret_1w"])).mean())
        week_weights[k] = round(float(np.clip(1 + n / (n + SHRINK) * (acc - 0.5) * 4, 0, 2)), 2)
        r, t = significance(live[col], live["ret_1w"])
        t *= np.sqrt(1 / 5)  # 每天記錄、看 5 天報酬，樣本重疊 → 有效樣本約 1/5
        if n >= MIN_SELECT_N and not (r > 0 and t >= MIN_T):
            week_removed[k] = f"{n} 筆實盤驗證不顯著（r={r:+.3f}, t={t:+.1f}）"
            week_weights[k] = 0.0
        week_stats[k] = {"acc": round(acc, 3), "n": n, "r": round(r, 4), "t": round(t, 2), "weight": week_weights[k]}

    scale = float(old.get("scale", 1.0))
    if len(done) >= MIN_SAMPLES_SCALE:
        raw_total = sum(done[f"f_{k}"].fillna(0) * weights[k] for k in FACTOR_KEYS if f"f_{k}" in done.columns)
        p_unscaled = done["stat_p"] + raw_total
        y = (done["ret_1d"] > 0).astype(float)
        brier = lambda s: float(((((50 + s * (p_unscaled - 50)).clip(15, 85)) / 100 - y) ** 2).mean())
        scale = round(float(min(np.arange(0.2, 1.51, 0.05), key=brier)), 2)
    return {"factors": weights, "scale": scale, "updated": today, "samples": int(len(done)),
            "factor_stats": stats, "previous_factors": old.get("factors", {}), "previous_scale": old.get("scale", 1.0),
            "removed": removed, "week_removed": week_removed,
            "week_factors": week_weights, "week_factor_stats": week_stats, "week_samples": int(len(week_done)),
            "previous_week_factors": old.get("week_factors", {})}


# ─────────────── 快照 ───────────────
def save_snapshots():
    holders = M.load_holders()
    if holders:
        date = next(iter(holders.values()))["date"]
        path = M.DATA_DIR / "holders" / f"{date}.csv"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            snap = pd.DataFrame([{"code": c, **v} for c, v in holders.items() if c.isdigit() or c.startswith("00")])
            snap.round(4).to_csv(path, index=False)
    prof = M.load_profitability()
    if prof:
        path = M.DATA_DIR / "profitability.csv"
        new = pd.DataFrame([{"code": c, **v} for c, v in prof.items()])
        if path.exists():
            old = pd.read_csv(path, dtype={"code": str})
            new = pd.concat([old, new]).drop_duplicates(["code", "quarter"], keep="last")
        new.round(3).to_csv(path, index=False)


# ─────────────── 檢討報告 ───────────────
def pct(x):
    return f"{x * 100:.1f}%" if x is not None else "—"


def build_review(today, preds, weights, today_preds):
    lines = [f"# 📝 每日檢討 {today}", ""]
    for col in ("target_date", "ret_1d", "ret_1w"):
        if col not in preds.columns:
            preds = preds.assign(**{col: np.nan})
    done = preds.dropna(subset=["ret_1d"])
    latest_target = done["target_date"].max() if not done.empty else None

    # 1. 最近一次驗證
    lines += ["## 1. 最近一次預測驗證", ""]
    misses = pd.DataFrame()
    if latest_target:
        batch = done[done["target_date"] == latest_target].copy()
        batch["方向"] = np.where(batch["final_p"] > 50, "上漲", np.where(batch["final_p"] < 50, "下跌", "盤整"))
        batch["結果"] = np.where(batch["final_p"] == 50, "—",
                               np.where((batch["final_p"] > 50) == (batch["ret_1d"] > 0), "✅", "❌"))
        lines += [f"預測日（依據收盤）{batch['base_date'].iloc[0]} → 驗證日 {latest_target}", "",
                  "| 股票 | 預測上漲機率 | 預測 | 實際漲跌 | 結果 |", "|---|---|---|---|---|"]
        for _, r in batch.iterrows():
            lines.append(f"| {r['name']}（{r['code']}） | {r['final_p']:.0f}% | {r['方向']} | {r['ret_1d']:+.2f}% | {r['結果']} |")
        misses = batch[batch["結果"] == "❌"]
        lines += ["", f"本次命中 {int((batch['結果'] == '✅').sum())} / {int((batch['結果'] != '—').sum())} 檔。", ""]
    else:
        lines += ["尚無可驗證的預測（第一次執行，明天開始會有驗證結果）。", ""]

    # 2. 累積命中率
    lines += ["## 2. 累積命中率", "", "| 範圍 | 樣本 | 模型命中率 | 永遠猜漲 | 高信心（≥60% 或 ≤40%）命中率 |", "|---|---|---|---|---|"]
    recent_dates = sorted(done["target_date"].dropna().unique())[-20:] if not done.empty else []
    for label, subset, p_col, r_col in [
        ("隔天：近 20 個交易日", done[done["target_date"].isin(recent_dates)], "final_p", "ret_1d"),
        ("隔天：全部", done, "final_p", "ret_1d"),
        ("隔天：只用統計（對照）", done, "stat_p", "ret_1d"),
        ("1 週：模型", preds, "week_p", "ret_1w"),
        ("1 週：只用統計（對照）", preds, "week_stat_p", "ret_1w"),
    ]:
        s = hit_stats(subset, p_col, r_col) if not subset.empty else None
        if s:
            lines.append(f"| {label} | {s['n']} | {pct(s['hit'])} | {pct(s['baseline'])} | {pct(s['conf_hit'])}（{s['conf_n']} 筆） |")
        else:
            lines.append(f"| {label} | 0 | — | — | — |")
    lines += ["", "> 模型命中率要明顯高於「永遠猜漲」才代表真的有預測能力。", ""]

    # 3. 錯誤分析
    lines += ["## 3. 為何與市場不同（錯誤案例分析）", ""]
    if misses.empty:
        lines += ["最近一次預測沒有失誤，或尚無資料。", ""]
    for _, r in misses.iterrows():
        right, wrong = [], []
        for k in FACTOR_KEYS:
            v = r.get(f"f_{k}", 0)
            if v and not np.isnan(v):
                (right if np.sign(v) == np.sign(r["ret_1d"]) else wrong).append(f"{M.FACTOR_LABELS[k]}（{v:+.1f}）")
        big = abs(r["ret_1d"]) >= BIG_MOVE
        lines += [f"### {r['name']}（{r['code']}）：預測上漲機率 {r['final_p']:.0f}%，實際 {r['ret_1d']:+.2f}%"
                  + ("　⚡ 大波動" if big else ""), "",
                  f"- 統計基礎機率 {r['stat_p']:.0f}%",
                  f"- 看錯方向的因子：{'、'.join(wrong) or '無（主要是統計基礎偏差）'}",
                  f"- 看對方向的因子：{'、'.join(right) or '無'}"]
        ctx_file = CONTEXT_DIR / f"{r['base_date']}.json"
        if ctx_file.exists():
            ctx = json.loads(ctx_file.read_text(encoding="utf-8")).get(r["code"], {})
            if ctx.get("news"):
                lines.append(f"- 當時的夜間新聞：{'；'.join(ctx['news'][:4])}")
        if big and not wrong:
            lines.append("- 模型訊號幾乎沒有預警，可能是盤中突發消息或模型尚未納入的因素")
        lines.append("")

    # 4. 因子表現與權重
    lines += ["## 4. 因子表現與權重調整", "",
              "| 因子 | 命中率（含歷史回填） | 樣本 | 實盤命中率 | 實盤樣本 | 權重 |", "|---|---|---|---|---|---|"]
    prev = weights.get("previous_factors", {})
    for k, s in sorted(weights["factor_stats"].items(), key=lambda kv: -(kv[1]["n"] or 0)):
        old_w = prev.get(k, 1.0)
        change = f"{old_w:.2f} → **{s['weight']:.2f}**" if abs(old_w - s["weight"]) >= 0.01 else f"{s['weight']:.2f}"
        lines.append(f"| {M.FACTOR_LABELS[k]} | {pct(s['acc'])} | {s['n']} | {pct(s.get('live_acc'))} | {s.get('live_n', 0)} | {change} |")
    lines += ["", f"整體機率縮放：{weights.get('previous_scale', 1.0)} → **{weights['scale']}**"
              f"（累積 {weights['samples']} 筆驗證；{MIN_SAMPLES_SCALE} 筆以上才開始校準）", "",
              "> 權重規則：命中率 50% = 1.0（維持），60% ≈ 1.4，40% ≈ 0.6，命中率越差越接近 0（等於停用）；"
              f"樣本越少越接近 1.0（收斂參數 {SHRINK}）。", ""]

    lines += ["### 1 週計分卡因子（以實際 5 個交易日報酬驗證）", "", "| 因子 | 命中率 | 樣本 | 權重 |", "|---|---|---|---|"]
    prev_w = weights.get("previous_week_factors", {})
    for k, s in weights.get("week_factor_stats", {}).items():
        old_w = prev_w.get(k, 1.0)
        change = f"{old_w:.2f} → **{s['weight']:.2f}**" if abs(old_w - s["weight"]) >= 0.01 else f"{s['weight']:.2f}"
        lines.append(f"| {WEEK_FACTOR_LABELS[k]} | {pct(s['acc'])} | {s['n']} | {change} |")
    lines += ["", "### 1 週 / 1 個月 / 1 年模型（每週一以 20 年回測重新校準）", "",
              "| 期間 | 採用 | 驗證期命中率 | 永遠猜漲 | 驗證期 Brier（模型 / 歷史比例） |", "|---|---|---|---|---|"]
    for h, hm in [(h, hm) for h, hm in M.load_horizon_model().items() if "test_hit" in hm]:
        lines.append(f"| {h} | {'✅ 回測校準模型' if hm['mode'] == 'model' else '⛔ 模型無預測力，改用歷史上漲比例'} | "
                     f"{pct(hm['test_hit'])} | {pct(hm['test_always_up'])} | {hm['test_brier']:.4f} / {hm['test_brier_base']:.4f} |")
    lines += ["", "> 1 週因子另外以實盤 5 日報酬每日微調；1 個月與 1 年的實盤紀錄（month_p / year_p）持續累積，供每週回測比對。", ""]

    # 6. 今日預測
    lines += ["## 6. 今日預測（台股開盤前，模型量化判斷）", "",
              "| 股票 | 隔天 | 1 週 | 1 個月 | 1 年 | 隔天統計基礎 |", "|---|---|---|---|---|---|"]
    arrow = lambda p: "📈" if p >= 55 else "📉" if p <= 45 else "➡️"
    for r in today_preds:
        cells = " | ".join(f"{arrow(r[c])} {r[c]:.0f}%" for c in ("final_p", "week_p", "month_p", "year_p"))
        lines.append(f"| {r['name']}（{r['code']}） | {cells} | {r['stat_p']:.0f}% |")
    return "\n".join(lines)


def ai_review(report):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    prompt = f"""你是量化研究員，負責檢討一套台股隔天漲跌預測模型。以下是今天的自動檢討報告：

{report}

請用繁體中文 Markdown 撰寫「## 5. AI 檢討」：
1. 逐檔說明失誤案例為何與市場不同（哪些因子失靈、可能遺漏了什麼資訊，例如盤中消息、類股輪動、大盤系統性風險）
2. 從累積命中率判斷模型目前是否真的優於「永遠猜漲」，若沒有要直說
3. 對因子權重的調整是否合理提出看法
4. 提出最多 3 點具體的模型改進建議（新增資料、修改規則），並說明如何驗證
只根據報告內容推論，不要捏造數據；保持客觀，不要過度解讀少量樣本。"""
    try:
        return M.ask_ai("Claude", key, AI_MODEL, prompt, max_tokens=3000)
    except Exception as e:
        return f"## 5. AI 檢討\n\n（AI 檢討失敗：{e}）"


# ─────────────── 主流程 ───────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()
    stocks = tracked_stocks()
    today = datetime.now(M.TAIPEI).date().isoformat()

    if args.smoke:
        for code in list(stocks)[:2]:
            rec, ctx = predict_stock(code, stocks[code])
            print(json.dumps(rec, ensure_ascii=False, default=str))
            print(pd.DataFrame(ctx["signals"]).to_string() if ctx else "no context")
            print(M.upcoming_events(code, datetime.now(M.TAIPEI).date()))
        holdings, date, top, errors = M.analyze_etf_constituents("0050.TW")
        print(f"::notice::0050 成分股 ({date})%0A" + top.to_string().replace("\n", "%0A") + "%0A錯誤：" + "；".join(errors))
        print("::notice::總經 " + json.dumps(M.load_macro(), ensure_ascii=False, default=str))
        rec, ctx = predict_stock("2330.TW", "台積電")
        print("::notice::台積電模型判斷 " + json.dumps(ctx["verdicts"], ensure_ascii=False)
              + f" 1週原始因子 {json.dumps({k: rec[f'w_{k}'] for k in WEEK_FACTOR_KEYS})}")
        print("revenue:", M.load_monthly_revenue().get("2330.TW"))
        print("profit:", M.load_profitability().get("2330.TW"))
        print("holders:", M.load_holders().get("2330"))
        print("put/call:", M.load_put_call()[:1])
        print("smoke test OK")
        return

    for folder in (M.DATA_DIR, REVIEW_DIR, CONTEXT_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    preds = pd.read_csv(PRED_FILE, dtype={"code": str}) if PRED_FILE.exists() else pd.DataFrame()
    if not preds.empty:
        preds = evaluate(preds)

    if args.backfill or not BACKFILL_FILE.exists():
        print("回填價格類因子歷史 ...")
        backfill_price_factors(stocks).to_csv(BACKFILL_FILE, index=False)
    backfill = pd.read_csv(BACKFILL_FILE) if BACKFILL_FILE.exists() else pd.DataFrame()

    old = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8")) if WEIGHTS_FILE.exists() else {}
    weights = calibrate(preds, backfill, old, today)
    WEIGHTS_FILE.write_text(json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8")

    # 用校準後的權重產生今天的預測 (同一檔同一個依據日只預測一次，遇到休市不重複)
    today_preds, contexts = [], {}
    for code, name in stocks.items():
        try:
            rec, ctx = predict_stock(code, name)
        except Exception as e:
            print(f"{code} 預測失敗：{e}")
            continue
        if rec is None:
            continue
        if not preds.empty and ((preds["code"] == code) & (preds["base_date"] == rec["base_date"])).any():
            continue
        today_preds.append(rec)
        contexts[code] = ctx
    if today_preds:
        preds = pd.concat([preds, pd.DataFrame(today_preds)], ignore_index=True)
        base_date = today_preds[0]["base_date"]
        (CONTEXT_DIR / f"{base_date}.json").write_text(json.dumps(contexts, ensure_ascii=False, default=str), encoding="utf-8")
    preds.to_csv(PRED_FILE, index=False)

    try:
        save_snapshots()
    except Exception as e:
        print(f"快照失敗：{e}")

    report = build_review(today, preds, weights, today_preds)
    ai_text = ai_review(report)
    if ai_text:
        report = report.replace("## 6. 今日預測", ai_text.strip() + "\n\n## 6. 今日預測")
    (REVIEW_DIR / f"{today}.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
