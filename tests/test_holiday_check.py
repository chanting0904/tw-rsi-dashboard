import datetime
import json
from pathlib import Path
import unittest

from holiday_check import missing_next_year
from publish_gate import last_trading_day_of_week

ROOT = Path(__file__).resolve().parents[1]


class HolidayCheckTest(unittest.TestCase):
    def test_december_without_next_year_warns(self):
        self.assertEqual(missing_next_year(datetime.date(2026, 12, 7), {'2026': []}), '2027')

    def test_no_warning_outside_december_or_when_present(self):
        self.assertIsNone(missing_next_year(datetime.date(2026, 11, 30), {'2026': []}))
        self.assertIsNone(missing_next_year(datetime.date(2026, 12, 7), {'2026': [], '2027': []}))

    def test_new_year_day_is_never_a_rebalance_day(self):
        calendar = json.loads((ROOT / 'holidays.json').read_text(encoding='utf-8'))
        holidays = {d for days in calendar.values() for d in days} | {'2027-01-01'}
        self.assertEqual(last_trading_day_of_week(datetime.date(2026, 12, 28), holidays), datetime.date(2026, 12, 31))


if __name__ == '__main__':
    unittest.main()
