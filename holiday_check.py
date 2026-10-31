# -*- coding: utf-8 -*-
"""12 月起每週一檢查明年休市表；缺少時用 Telegram 提醒，避免 1/1 起停止發布。"""
import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TW = ZoneInfo("Asia/Taipei")


def missing_next_year(today, calendar):
    """12 月且休市表沒有明年時，回傳缺少的年份；其他情況回傳 None。"""
    if today.month != 12:
        return None
    year = str(today.year + 1)
    return None if year in calendar else year


def main():
    today = datetime.datetime.now(TW).date()
    if today.weekday() != 0:
        return
    calendar = json.loads((ROOT / "holidays.json").read_text(encoding="utf-8"))
    year = missing_next_year(today, calendar)
    if not year:
        return
    from tg_push_v5 import tg_send
    tg_send(f"⚠️ 休市表還沒有 {year} 年。請在證交所公布「開休市日期」後補進 holidays.json；"
            f"沒補的話，{year}-01-01 起會停止產生調倉計畫。")


if __name__ == "__main__":
    main()
