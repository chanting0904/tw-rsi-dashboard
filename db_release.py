# -*- coding: utf-8 -*-
"""資料庫存放在 GitHub Release（local-db），不進 git 歷史。

    python db_release.py download   # 下載全部資料庫到 local_db/（雲端每次執行前；本機要同步時也可用）
    python db_release.py upload     # 只上傳這次有變動的檔案
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "local_db"
TAG = "local-db"
MANIFEST = DB / ".release_manifest.json"
REQUIRED = {"amount.feather", "basic_info.feather", "capital.feather", "close.feather", "roe.feather",
            "volume.feather", "history_prices.sqlite3", "history_reference.sqlite3", "official.sqlite3"}


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def files():
    return sorted(p for p in DB.iterdir() if p.is_file() and p.suffix in (".feather", ".sqlite3"))


def download():
    DB.mkdir(exist_ok=True)
    subprocess.run(["gh", "release", "download", TAG, "-D", str(DB), "--clobber"], check=True, cwd=ROOT)
    missing = REQUIRED - {p.name for p in files()}
    if missing:
        raise SystemExit("Release 缺少資料庫檔案：" + ", ".join(sorted(missing)))
    MANIFEST.write_text(json.dumps({p.name: digest(p) for p in files()}, indent=1), encoding="utf-8")
    print("資料庫已下載：", len(files()), "個檔案")


def upload():
    if not MANIFEST.exists():
        print("本次沒有成功下載資料庫，不上傳（避免覆蓋）")
        return
    before = json.loads(MANIFEST.read_text(encoding="utf-8"))
    changed = [p for p in files() if before.get(p.name) != digest(p)]
    if not changed:
        print("資料庫沒有變動")
        return
    subprocess.run(["gh", "release", "upload", TAG, *[str(p) for p in changed], "--clobber"], check=True, cwd=ROOT)
    MANIFEST.write_text(json.dumps({p.name: digest(p) for p in files()}, indent=1), encoding="utf-8")
    print("資料庫已上傳：", ", ".join(p.name for p in changed))


if __name__ == "__main__":
    {"download": download, "upload": upload}[sys.argv[1]]()
