# -*- coding: utf-8 -*-
"""Offline syntax and test checks; never sends alerts or changes signal files."""
from pathlib import Path
import py_compile
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    for source in sorted(ROOT.glob("*.py")) + sorted((ROOT / "s5_live").glob("*.py")) + sorted((ROOT / "v5_engine").glob("*.py")):
        py_compile.compile(str(source), doraise=True)
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)
    javascript = sorted((ROOT / 'tests').glob('*.cjs'))
    result = subprocess.run(['node', '--test', *map(str,javascript)], cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)
    print("PREFLIGHT PASS：語法、日期與發布檢查通過；未連網、未發送、未更改持倉。")


if __name__ == "__main__":
    main()
