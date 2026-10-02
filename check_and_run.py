# -*- coding: utf-8 -*-
"""每天 06:00 觸發：僅「週五」才執行完整選股（週五休市 → 該週跳過，與回測一致）。
FORCE_FULL=1 可手動強制（workflow_dispatch full 用）。"""
import datetime, json, os, subprocess, sys
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
FORCE = os.environ.get("FORCE_FULL") == "1"


def load_holidays():
    try:
        h = json.load(open(os.path.join(ROOT, "holidays.json"), encoding="utf-8"))
        s = set()
        for v in h.values():
            s.update(v)
        return s
    except Exception:
        return set()


HOLS = load_holidays()


def trading(d):
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in HOLS


def main():
    today = datetime.datetime.now(ZoneInfo("Asia/Taipei")).date()
    is_fri = trading(today) and today.weekday() == 4  # 僅週五調倉；週五休市→該週跳過
    if FORCE or is_fri:
        print(f"[full] {today} 為週五調倉日，執行完整選股")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "update_data.py")])
        sys.exit(r.returncode)
    else:
        print(f"[skip] {today} 非週五調倉日（{today.strftime('%A')}），本日不選股")


if __name__ == "__main__":
    main()
