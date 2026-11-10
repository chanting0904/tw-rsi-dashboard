"""Evening publication and morning recovery share the same verified weekly plan."""
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from publication_check import verify_public_index
from publish_gate import evaluate, fingerprint, last_trading_day_of_week, load_holidays, prev_trading_day_str

ROOT = Path(__file__).resolve().parent


def recovery_mode(today=None, *, evening=False, daily=False, now=None):
    if now is None:
        now = dt.datetime.combine(today, dt.time(21)) if today else dt.datetime.now(ZoneInfo("Asia/Taipei"))
    today = today or now.date()
    holidays = load_holidays()
    execution_day = last_trading_day_of_week(today, holidays)
    release_day = (dt.date.fromisoformat(prev_trading_day_str(execution_day, holidays))
                   if execution_day else None)
    if evening:
        # GitHub 排程可能延遲數小時：中午前才開始的晚間排程，屬於前一天晚上。
        # 延到隔天清晨仍要補發本週計畫；前一晚的延遲排程不可提早發布當天計畫。
        scheduled_day = today if now.hour >= 12 else today - dt.timedelta(days=1)
        due = scheduled_day == release_day and today <= execution_day
    else:
        due = today == execution_day
    if not due:
        return ("daily", "只更新日常行情") if daily else ("skip", "今天不是本週發布或補救日")
    gate = evaluate(today=today, notify=False)
    if not gate["ok"]:
        return "full", "本週訊號尚未完成或驗證未通過，補跑資料與選股"
    signal = json.loads((ROOT / "output/latest_signals.json").read_text(encoding="utf-8"))
    try:
        status = json.loads((ROOT / "output/weekly_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = {}
    if (status.get("state") != "ready" or status.get("rebalance_date") != execution_day.isoformat()
            or status.get("canonical_hash") != fingerprint(signal)):
        return "publish", "訊號已完成，補做網站完成狀態與發布"
    try:
        verify_public_index(attempts=1, write_receipt=False)
    except (OSError, ValueError, RuntimeError):
        return "publish", "訊號已完成，補做公開網站發布"
    return "skip", "本週訊號與公開網頁均已完成"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--evening", action="store_true")
    parser.add_argument("--daily", action="store_true")
    args = parser.parse_args()
    mode, reason = recovery_mode(evening=args.evening, daily=args.daily)
    print(reason)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("mode=" + mode + "\n")
