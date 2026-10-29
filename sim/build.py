"""產生測試網頁：python sim/build.py → sim/dist/（index.html + pyodide/）

把 reminderbot 的核心程式碼原封不動塞進網頁，瀏覽器用 Pyodide 執行，
所以測試網頁的判斷邏輯和正式上線的 bot 完全一樣。
"""
import base64
import io
import json
import shutil
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "sim"
DIST = SIM / "dist"
CACHE = SIM / ".pyodide-cache"
PYODIDE_VERSION = "0.26.4"
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]
# 瀏覽器裡沒有 discord.py／aiohttp，這幾個不打包；其他 reminderbot/*.py 全部塞進網頁
SKIP = {"bot.py", "web.py", "config.py", "__main__.py"}


def fetch_pyodide():
    if all((CACHE / f).exists() for f in PYODIDE_FILES):
        return
    url = f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz"
    print("下載", url)
    data = urllib.request.urlopen(url, timeout=120).read()
    CACHE.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for f in PYODIDE_FILES:
            (CACHE / f).write_bytes(tar.extractfile(f"package/{f}").read())


def main():
    fetch_pyodide()
    src = {f"reminderbot/{p.name}": p.read_text(encoding="utf-8")
           for p in sorted((ROOT / "reminderbot").glob("*.py")) if p.name not in SKIP}
    src["simbridge.py"] = (SIM / "simbridge.py").read_text(encoding="utf-8")
    payload = json.dumps(src, ensure_ascii=False).replace("</", "<\\/")
    preset = json.dumps((SIM / "preset.txt").read_text(encoding="utf-8"), ensure_ascii=False).replace("</", "<\\/")
    html = (SIM / "template.html").read_text(encoding="utf-8").replace("/*__PYSRC__*/", payload) \
        .replace("/*__PRESET__*/", preset)
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "pyodide").mkdir(parents=True)
    (DIST / "index.html").write_text(html, encoding="utf-8")
    for f in PYODIDE_FILES:
        if f == "python_stdlib.zip":
            # 網頁空間不能放 .zip，所以包成 .js（base64），網頁再攔截 Pyodide 的下載請求交給它
            b64 = base64.b64encode((CACHE / f).read_bytes()).decode()
            (DIST / "pyodide" / "python_stdlib.js").write_text(f'self.PY_STDLIB_B64="{b64}";')
        else:
            shutil.copy(CACHE / f, DIST / "pyodide" / f)
    total = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    print(f"完成：{DIST}（共 {total / 1e6:.1f} MB）")


if __name__ == "__main__":
    main()
