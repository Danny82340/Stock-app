"""CI 用：在無畫面環境實際執行 app.py，檢查首頁與個股分析 (台積電、0050) 都沒有錯誤
(結果以 GitHub annotation 輸出，公開 repo 不需登入就能在 API 看到)"""
import sys
import traceback

from streamlit.testing.v1 import AppTest


def note(level, msg):
    print(f"::{level}::" + str(msg).replace("\n", "%0A")[:3000])


def run(label, state):
    try:
        at = AppTest.from_file("../app.py", default_timeout=300)
        for k, v in state.items():
            at.session_state[k] = v
        at.run()
    except Exception:
        note("error", f"{label} AppTest 執行失敗: {traceback.format_exc()}")
        return False
    errors = [e.value for e in at.error]
    exceptions = [f"{e.message}\n{''.join(e.stack_trace) if e.stack_trace else ''}" for e in at.exception]
    metrics = "; ".join(f"{m.label}={m.value}" for m in at.metric[:40])
    note("notice", f"{label}: {len(at.metric)} 指標, {len(at.dataframe)} 表格, {len(at.tabs)} 分頁 | {metrics}")
    for d in at.dataframe:
        try:
            v = d.value.data if hasattr(d.value, "data") else d.value  # Styler → DataFrame
            if {"成分股", "因子", "股票"} & set(v.columns):
                note("notice", f"{label} 表格:%0A{v.drop(columns=[c for c in v.columns if '走勢' in c]).head(10).to_string()}")
        except Exception:
            pass
    for e in errors:
        note("error", f"{label} st.error: {e}")
    for e in exceptions:
        note("error", f"{label} 例外: {e}")
    return not (errors or exceptions or len(at.metric) == 0)


ok = run("首頁", {})
for code in ["2330.TW", "0050.TW"]:
    ok &= run(code, {f"chk_{code}": True, "page": "🔎 個股分析"})
sys.exit(0 if ok else 1)
