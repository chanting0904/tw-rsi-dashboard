import unittest
import pandas as pd
from v4_engine.corporate_actions import CorporateLedger, build_events
from v4_engine.performance import trade_cycles


class CorporateActionTests(unittest.TestCase):
    def test_dividend_not_spendable_before_payment_and_survives_sale(self):
        ex,payment=pd.Timestamp('2026-01-05'),pd.Timestamp('2026-01-20')
        ledger=CorporateLedger({ex:{'2330':{'cash':5.,'payment':payment}}})
        pos={'2330':{'shares':100,'avg':100.,'pool':'A'}}
        cash,records=ledger.advance(ex,pos,1000,{'2330':95})
        self.assertEqual(cash,1000)
        self.assertEqual(ledger.value,500)
        self.assertEqual(cash+ledger.value+100*95,11000)
        cash,_=ledger.advance(ex+pd.Timedelta(days=1),{},cash,{})
        self.assertEqual(cash,1000)
        cash,_=ledger.advance(payment,{},cash,{})
        self.assertEqual(cash,1500)
        self.assertEqual(ledger.value,0)

    def test_split_preserves_value_and_does_not_create_cash(self):
        day=pd.Timestamp('2026-01-05')
        ledger=CorporateLedger({day:{'2330':{'ratio':4.,'kind':'SPLIT'}}})
        pos={'2330':{'shares':11,'avg':100.,'pool':'A'}}
        cash,records=ledger.advance(day,pos,1000,{'2330':25})
        self.assertEqual(pos['2330']['shares'],44)
        self.assertEqual(pos['2330']['avg'],25)
        self.assertEqual(cash,1000)
        self.assertEqual(records[0]['shares'],33)

    def test_no_entitlement_to_stock_bought_on_ex_date(self):
        day=pd.Timestamp('2026-01-05')
        ledger=CorporateLedger({day:{'2330':{'cash':5.,'payment':day}}})
        cash,records=ledger.advance(day,{},1000,{'2330':95})
        self.assertEqual(cash,1000)
        self.assertEqual(records,[])

    def test_unknown_payment_is_asset_not_cash(self):
        day=pd.Timestamp('2026-01-05')
        ledger=CorporateLedger({day:{'2330':{'cash':5.,'payment':None}}})
        cash,_=ledger.advance(day,{'2330':{'shares':10,'pool':'A'}},1000,{'2330':95})
        self.assertEqual((cash,ledger.value),(1000,50))

    def test_unresolved_company_event_stops_instead_of_inventing_profit(self):
        day=pd.Timestamp('2026-01-05')
        ledger=CorporateLedger({day:{'2330':{'unresolved_factor':.8}}})
        with self.assertRaises(ValueError):
            ledger.advance(day,{'2330':{'shares':100}},0,{'2330':100})

    def test_cycle_includes_dividend_and_split_without_extra_trading_fee(self):
        dates=pd.date_range('2026-01-01',periods=4)
        fills=pd.DataFrame([{'date':d,'code':'2330','pool':'A','rank':1,'side':side,'shares':q,'price':p,'amount':a}
            for d,side,q,p,a in zip(dates,['BUY','DIVIDEND','SHARE_ADJUST','SELL'],[10,0,10,20],[100,0,0,50],[1000,100,0,1000])])
        result=trade_cycles(fills,pd.DataFrame({'2330':[100,90,45,50]},index=dates),0,0)
        self.assertEqual(result.iloc[0]['pnl'],100)

    def test_exchange_closure_moves_entitlement_to_next_actual_session(self):
        days=pd.to_datetime(['2026-01-08','2026-01-12'])
        raw=pd.DataFrame({'2330':[100.,90.]},index=days)
        adjusted=pd.DataFrame({'2330':[100.,100.]},index=days)
        ann=pd.DataFrame([{'symbol':'2330','公告日期':pd.Timestamp('2025-12-01'),
            '除息交易日':pd.Timestamp('2026-01-09'),'現金股利發放日':pd.Timestamp('2026-02-01'),
            '盈餘分配之股東現金股利(元/股)':10.}])
        events=build_events(ann,raw,adjusted)
        self.assertEqual(events[days[1]]['2330']['cash'],10)
        self.assertNotIn('unresolved_factor',events[days[1]]['2330'])

if __name__=='__main__':
    unittest.main()
