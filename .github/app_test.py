"""CI 用：在無畫面環境實際執行 app.py，選取台積電與 0050，檢查畫面沒有錯誤"""
import sys

from streamlit.testing.v1 import AppTest

failed = False
for code in ["2330.TW", "0050.TW"]:
    at = AppTest.from_file("app.py", default_timeout=300)
    at.session_state[f"chk_{code}"] = True
    at.run()
    errors = [e.value for e in at.error]
    exceptions = [str(e.value) for e in at.exception]
    print(f"== {code}: {len(at.metric)} 個指標卡片, {len(at.dataframe)} 個表格, 錯誤 {len(errors)}, 例外 {len(exceptions)}")
    for m in at.metric[:12]:
        print(f"   {m.label}: {m.value} | {m.delta}")
    for e in errors + exceptions:
        print(f"   ❌ {e}")
    if errors or exceptions or len(at.metric) == 0:
        failed = True

sys.exit(1 if failed else 0)
