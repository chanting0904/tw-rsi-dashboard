# -*- coding: utf-8 -*-
"""從證交所「市場開休市日期」自動更新 holidays.json。

今年與明年都查；證交所一公布（通常 10–12 月）就寫入，推估的日期也會被官方版本取代。
只保留落在平日的休市日（含「市場無交易，僅辦理結算交割」），與原本 holidays.json 的定義相同。
"""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
HOLIDAYS = ROOT / "holidays.json"
URL = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"


def closed_weekdays(document, year):
    """官方表尚未公布回傳 None；格式異常直接報錯，不寫入可疑資料。"""
    if str(document.get("queryYear")) != str(year) or not document.get("data"):
        return None
    days = set()
    for row in document["data"]:
        day, name = str(row[0]).strip(), str(row[1]).strip()
        if name.endswith("交易日"):          # 「春節前最後交易日」「開始交易日」當天有交易
            continue
        date = dt.date.fromisoformat(day)
        if date.year != year:
            raise ValueError(f"{year} 年休市表混入其他年份：{day}")
        if date.weekday() < 5:
            days.add(day)
    new_year = dt.date(year, 1, 1)
    if new_year.weekday() < 5 and new_year.isoformat() not in days:
        raise ValueError(f"{year} 年官方休市表缺少元旦，不採用")
    if len(days) < 8:
        raise ValueError(f"{year} 年官方休市表只有 {len(days)} 天，不採用")
    return sorted(days)


def fetch(year):
    response = requests.get(URL, params={"date": f"{year}0101", "response": "json"},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    return response.json()


def sync(calendar, years, getter=fetch):
    """回傳 (新的 calendar, 有變動的年份)。"""
    updated, changed = dict(calendar), []
    for year in years:
        days = closed_weekdays(getter(year), year)
        if days is not None and updated.get(str(year)) != days:
            updated[str(year)] = days
            changed.append(year)
    return dict(sorted(updated.items())), changed


def dump(calendar):
    body = ",\n".join(f'  "{year}": {json.dumps(days)}' for year, days in calendar.items())
    return "{\n" + body + "\n}\n"


def main():
    today = dt.datetime.now(ZoneInfo("Asia/Taipei")).date()
    calendar = json.loads(HOLIDAYS.read_text(encoding="utf-8"))
    updated, changed = sync(calendar, [today.year, today.year + 1])
    if not changed:
        print("休市表已是證交所最新版本")
        return
    HOLIDAYS.write_text(dump(updated), encoding="utf-8")
    text = "、".join(f"{year} 年（{len(updated[str(year)])} 天）" for year in changed)
    print("休市表已依證交所公告更新：", text)
    try:
        from tg_push_v5 import tg_send
        tg_send(f"📅 已自動依證交所公告更新休市表：{text}")
    except Exception as error:   # 通知失敗不影響寫入
        print("TG 通知失敗：", error)


if __name__ == "__main__":
    main()
