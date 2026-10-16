from types import SimpleNamespace
import unittest
import pandas as pd
from v4_engine.config import StrategyConfig
from v4_engine.recommendations import recommendation_metadata


class RecommendationTests(unittest.TestCase):
    def sample(self, code='3089', day='2026-10-16'):
        day = pd.Timestamp(day)
        basis = day-pd.Timedelta(days=1)
        prices = pd.DataFrame({code:[81.,float('nan'),float('nan')]},
                              index=pd.to_datetime(['2023-07-19',basis,day]))
        ctx = SimpleNamespace(config=StrategyConfig(), close=prices, live_day=day, signal_day=basis)
        wp = {'date':day,'nav':100000.,'stock_exposure':.95}
        row = {'code':code,'target_val':0.,'cur_sh':16,'target_sh':0,'action':'HOLD',
               'target_weight':0.,'cur_val':1296.}
        return ctx,wp,row

    def test_delisted_asset_has_true_quote_date_and_no_allocation(self):
        ctx,wp,row = self.sample()
        item = recommendation_metadata(ctx,wp,row)
        self.assertFalse(item['allocation_eligible'])
        self.assertEqual(item['trading_status'],'DELISTED')
        self.assertEqual(item['known_name'],'億杰')
        self.assertEqual(item['price_date'],'2023-07-19')
        self.assertIn('不列入下單',item['recommendation_note'])

    def test_security_event_only_applies_from_effective_day(self):
        ctx,wp,row = self.sample(day='2023-07-19')
        ctx.close.loc[ctx.signal_day,'3089'] = 81.
        row.update(target_val=2000.,target_sh=24)
        self.assertEqual(recommendation_metadata(ctx,wp,row)['trading_status'],'TRADABLE')
        ctx,wp,row = self.sample(day='2023-07-21')
        self.assertEqual(recommendation_metadata(ctx,wp,row)['trading_status'],'SUSPENDED')

    def test_missing_quote_without_registry_entry_is_still_not_allocated(self):
        ctx,wp,row = self.sample(code='9998')
        row.update(target_val=2000.,target_sh=24)
        item = recommendation_metadata(ctx,wp,row)
        self.assertFalse(item['allocation_eligible'])
        self.assertEqual(item['trading_status'],'NO_CURRENT_QUOTE')

    def test_partial_new_purchase_explains_cap_without_changing_size(self):
        ctx,wp,row = self.sample(code='6505')
        ctx.close.loc[ctx.signal_day,'6505'] = 111.5
        row.update(action='BUY',target_val=1500.,target_sh=13,cur_sh=1,cur_val=111.5,target_weight=.015)
        item = recommendation_metadata(ctx,wp,row)
        self.assertTrue(item['allocation_eligible'])
        self.assertIn('95%倉位上限',item['recommendation_note'])
        self.assertIn('1.50%',item['recommendation_note'])
        self.assertEqual(row['cur_sh'],1)

    def test_small_existing_position_is_not_described_as_partial_new_buy(self):
        ctx,wp,row = self.sample(code='6505')
        ctx.close.loc[ctx.signal_day,'6505'] = 111.5
        row.update(target_val=1500.,target_sh=13,cur_sh=1)
        self.assertEqual(recommendation_metadata(ctx,wp,row)['recommendation_note'],'')


if __name__ == '__main__':
    unittest.main()
