import json
from pathlib import Path
import unittest

from holiday_sync import closed_weekdays, dump, sync

ROOT = Path(__file__).resolve().parents[1]

# 證交所 115 年開休市表（節錄自官方 API 回應的欄位格式）
OFFICIAL_2026 = {"queryYear": 2026, "data": [
    ["2026-01-01", "中華民國開國紀念日", ""], ["2026-01-02", "國曆新年開始交易日", ""],
    ["2026-02-11", "農曆春節前最後交易日", ""],
    ["2026-02-12", "市場無交易，僅辦理結算交割作業", ""], ["2026-02-13", "市場無交易，僅辦理結算交割作業", ""],
    *[[f"2026-02-{d}", "農曆除夕及春節", ""] for d in (15, 16, 17, 18, 19, 20)],
    ["2026-02-23", "農曆春節後開始交易日", ""],
    ["2026-02-27", "和平紀念日", ""], ["2026-02-28", "和平紀念日", ""],
    ["2026-04-03", "兒童節及民族掃墓節", ""], ["2026-04-04", "兒童節及民族掃墓節", ""],
    ["2026-04-05", "兒童節及民族掃墓節", ""], ["2026-04-06", "兒童節及民族掃墓節", ""],
    ["2026-05-01", "勞動節", ""], ["2026-06-19", "端午節", ""], ["2026-09-25", "中秋節", ""],
    ["2026-09-28", "孔子誕辰紀念日/ 教師節", ""], ["2026-10-09", "國慶日", ""], ["2026-10-10", "國慶日", ""],
    ["2026-10-25", "臺灣光復暨金門古寧頭大捷紀念日", ""], ["2026-10-26", "臺灣光復暨金門古寧頭大捷紀念日", ""],
    ["2026-12-25", "行憲紀念日", ""]]}


class HolidaySyncTest(unittest.TestCase):
    def test_official_2026_table_matches_existing_calendar(self):
        calendar = json.loads((ROOT / "holidays.json").read_text(encoding="utf-8"))
        self.assertEqual(closed_weekdays(OFFICIAL_2026, 2026), calendar["2026"])

    def test_unpublished_year_returns_none(self):
        self.assertIsNone(closed_weekdays({"queryYear": 2028, "data": []}, 2028))
        self.assertIsNone(closed_weekdays(OFFICIAL_2026, 2027))   # API 退回別的年份

    def test_suspicious_table_is_rejected(self):
        with self.assertRaises(ValueError):
            closed_weekdays({"queryYear": 2026, "data": [["2026-05-01", "勞動節", ""]]}, 2026)

    def test_sync_replaces_estimated_year_and_round_trips(self):
        calendar, changed = sync({"2026": ["2026-01-01"]}, [2026, 2027],
                                 getter=lambda year: OFFICIAL_2026 if year == 2026 else {"queryYear": 2027, "data": []})
        self.assertEqual(changed, [2026])
        self.assertEqual(json.loads(dump(calendar)), calendar)


if __name__ == "__main__":
    unittest.main()
