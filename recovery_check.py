"""07:00 safety pass: do only the unfinished work on the weekly trading day."""
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from publication_check import verify_public_index
from publish_gate import evaluate, fingerprint, last_trading_day_of_week, load_holidays

ROOT = Path(__file__).resolve().parent


def recovery_mode(today=None):
    today = today or dt.datetime.now(ZoneInfo("Asia/Taipei")).date()
    if today != last_trading_day_of_week(today, load_holidays()):
        return "skip", "今天不是本週最後交易日，毋須補跑"
    gate = evaluate(today=today, notify=False)
    if not gate["ok"]:
        return "full", "本週訊號尚未完成或驗證未通過，補跑資料與選股"
    signal = json.loads((ROOT / "output/latest_signals.json").read_text(encoding="utf-8"))
    try:
        state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    try:
        status = json.loads((ROOT / "output/weekly_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = {}
    if (status.get("state") != "ready" or status.get("rebalance_date") != today.isoformat()
            or status.get("canonical_hash") != fingerprint(signal)):
        return "publish", "訊號已完成，補做網站完成狀態與發布"
    try:
        verify_public_index(attempts=1, write_receipt=False)
    except (OSError, ValueError, RuntimeError):
        return "publish", "訊號已完成，補做公開網站發布與通知"
    if state.get("last_pushed_key") != fingerprint(signal):
        return "publish", "網站已完成，補做尚未完成的 Telegram 通知"
    return "skip", "本週訊號、公開網頁與 Telegram 均已完成"


if __name__ == "__main__":
    mode, reason = recovery_mode()
    print(reason)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("mode=" + mode + "\n")
