import copy
import unittest

from tg_push_v4 import build_message
from v4_engine.recommendations import hidden_zero_asset_codes, public_warnings


def sample():
    return {'actions': [], 'restricted_assets': [
        {'code': '3089', 'name': '歷史資產', 'shares': 1057, 'book_value': 0, 'policy': 'zero'}],
        'warnings': ['有 1 檔持股因停牌、跌停、缺報價或待入帳限制無法全部賣出，仍暫留模擬紀錄：3089']}


class PublicAssetNoticesTests(unittest.TestCase):
    def test_zero_history_and_its_repeated_warning_are_hidden_from_telegram(self):
        signal = sample()
        before = copy.deepcopy(signal)
        message = build_message(signal)
        self.assertNotIn('3089', message)
        self.assertNotIn('歷史受限資產', message)
        self.assertEqual(public_warnings(signal), [])
        self.assertEqual(signal, before)

    def test_mixed_warning_keeps_live_code_and_correct_count(self):
        signal = sample()
        signal['warnings'][0] = signal['warnings'][0].replace('1 檔', '2 檔') + '、1101'
        warnings = public_warnings(signal)
        self.assertEqual(len(warnings), 1)
        self.assertIn('有 1 檔', warnings[0])
        self.assertTrue(warnings[0].endswith('：1101'))
        self.assertNotIn('3089', warnings[0])

    def test_nonzero_restricted_asset_is_still_reported(self):
        signal = sample()
        signal['restricted_assets'][0]['book_value'] = 100
        self.assertEqual(hidden_zero_asset_codes(signal), set())
        self.assertIn('3089', build_message(signal))
        self.assertEqual(public_warnings(signal), signal['warnings'])

    def test_unknown_or_nonzero_policy_is_not_treated_as_written_off(self):
        for value, policy in [(None, 'zero'), (float('nan'), 'zero'), (0, 'last_quote'), (False, 'zero')]:
            signal = sample()
            signal['restricted_assets'][0].update(book_value=value, policy=policy)
            self.assertEqual(hidden_zero_asset_codes(signal), set())

    def test_executable_stock_or_real_sale_is_never_hidden_by_old_history(self):
        for row in [{'stock_code': '3089', 'allocation_eligible': True},
                    {'stock_code': '3089', 'allocation_eligible': False,
                     'action': 'SELL', 'estimated_sell_amount': 100}]:
            signal = sample()
            signal['actions'] = [row]
            self.assertEqual(hidden_zero_asset_codes(signal), set())

    def test_other_warnings_and_pending_entitlements_are_preserved(self):
        signal = sample()
        signal['warnings'].append('待領配股尚未入帳，請勿賣出未入帳股數。')
        signal['pending_assets'] = [{'code': '1101', 'shares': 10}]
        message = build_message(signal)
        self.assertIn('待領配股', message)
        self.assertIn('尚未入帳', message)
        self.assertNotIn('3089', message)


if __name__ == '__main__':
    unittest.main()
