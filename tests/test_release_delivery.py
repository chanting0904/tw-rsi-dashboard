import datetime
import unittest
from tg_push_v4 import delivery_due


class ReleaseDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.friday = datetime.date(2026, 10, 16)
        self.saturday = datetime.date(2026, 10, 17)

    def test_weekly_trading_day_is_due(self):
        self.assertTrue(delivery_due(self.friday, self.friday))

    def test_ordinary_weekend_is_not_due(self):
        self.assertFalse(delivery_due(self.saturday, self.friday, event_name='schedule'))

    def test_release_push_on_weekend_is_due(self):
        self.assertTrue(delivery_due(self.saturday, self.friday, event_name='push'))

    def test_explicit_recovery_is_due(self):
        self.assertTrue(delivery_due(self.saturday, self.friday, force=True))
