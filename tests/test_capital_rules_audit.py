import unittest

from v4_engine.allocation import preview_allocation


class CapitalRulesAuditTests(unittest.TestCase):
    def test_extreme_rounding_repair_does_not_exceed_safe_whole_shares(self):
        result = preview_allocation([
            {'code': '1101', 'target_weight': .5, 'price': .000105},
            {'code': '1102', 'target_weight': .45, 'price': 450000000000},
        ], 1000000000000)
        self.assertTrue(all(isinstance(qty, int) and 0 <= qty <= 2**53-1
                            for qty in result['quantities'].values()))
        self.assertGreaterEqual(result['cash'], 0)
        self.assertLessEqual(result['exposure'], .95 + 1e-10)
        self.assertAlmostEqual(result['cash'] + result['invested'] + result['fees'],
                               1000000000000, delta=.01)
