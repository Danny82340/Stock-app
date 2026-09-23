"""CI 用：在無畫面環境實際執行 app.py，選取台積電與 0050，檢查畫面沒有錯誤
(結果以 GitHub annotation 輸出，公開 repo 不需登入就能在 API 看到)"""
import sys
import traceback

from streamlit.testing.v1 import AppTest


def note(level, msg):
    print(f"::{level}::" + str(msg).replace("\n", "%0A")[:3000])


failed = False
for code in ["2330.TW", "0050.TW"]:
    try:
        at = AppTest.from_file("../app.py", default_timeout=300)
        at.session_state[f"chk_{code}"] = True
        at.run()
    except Exception:
        note("error", f"{code} AppTest 執行失敗: {traceback.format_exc()}")
        failed = True
        continue
    errors = [e.value for e in at.error]
    exceptions = [f"{e.message}\n{''.join(e.stack_trace) if e.stack_trace else ''}" for e in at.exception]
    metrics = "; ".join(f"{m.label}={m.value}" for m in at.metric[:14])
    note("notice", f"{code}: {len(at.metric)} 指標, {len(at.dataframe)} 表格, {len(at.tabs)} 分頁 | {metrics}")
    for d in at.dataframe:
        try:
            v = d.value.data if hasattr(d.value, "data") else d.value  # Styler → DataFrame
            if "成分股" in v.columns or "因子" in v.columns:
                note("notice", f"{code} 表格:%0A{v.head(10).to_string()}")
        except Exception:
            pass
    for e in errors:
        note("error", f"{code} st.error: {e}")
    for e in exceptions:
        note("error", f"{code} 例外: {e}")
    if errors or exceptions or len(at.metric) == 0:
        failed = True

sys.exit(1 if failed else 0)
