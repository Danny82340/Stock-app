"""CI 用：把執行紀錄的最後一段輸出成 GitHub error annotation (公開 API 可查看)"""
import sys

text = open(sys.argv[1], encoding="utf-8", errors="replace").read()[-5000:]
print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
