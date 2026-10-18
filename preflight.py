# -*- coding: utf-8 -*-
"""Offline syntax and message checks; never sends Telegram or changes signal/state files."""
import json
from pathlib import Path
import py_compile
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    for source in sorted(ROOT.glob("*.py")) + sorted((ROOT / "s5_live").glob("*.py")) + sorted((ROOT / "v4_engine").glob("*.py")):
        py_compile.compile(str(source), doraise=True)
    from tg_push_v4 import build_message, message_chunks
    signal = json.loads((ROOT / "output/latest_signals.json").read_text(encoding="utf-8"))
    chunks = list(message_chunks(build_message(signal)))
    if not chunks:
        raise RuntimeError("Telegram 訊息為空")
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)
    javascript = sorted((ROOT / 'tests').glob('*.cjs'))
    result = subprocess.run(['node', '--test', *map(str,javascript)], cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)
    print("PREFLIGHT PASS：語法、日期、發布檢查與訊息測試通過；未連網、未發送、未更改持倉。")


if __name__ == "__main__":
    main()
