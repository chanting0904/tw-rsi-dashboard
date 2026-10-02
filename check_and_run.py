# -*- coding: utf-8 -*-
"""每天 06:00 觸發：僅「本週最後交易日」才執行完整選股
（週五休市 → 提前週四，與回測 w5_close 一致）。
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
    # 本週最後交易日：今天是交易日且明天非交易日（週五休市 → 提前週四）
    is_last_td = trading(today) and not trading(today + datetime.timedelta(days=1))
    if FORCE or is_last_td:
        print(f"[full] {today} 為本週最後交易日，執行完整選股")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "update_data.py")])
        sys.exit(r.returncode)
    else:
        print(f"[skip] {today} 非本週最後交易日（{today.strftime('%A')}），本日不選股")


if __name__ == "__main__":
    main()
