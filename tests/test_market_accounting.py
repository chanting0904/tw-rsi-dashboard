import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from v5_engine.config import StrategyConfig
from v5_engine.fees import order_cost,OrderFees
from v5_engine.corporate_actions import CorporateLedger,delivery_registry
from v5_engine.security_status import status_at,can_trade,table_rows,refresh_security_status,date_string,snapshot_hash,build_snapshot,SOURCES
from v5_engine.valuation import prepare_valuation
from v5_engine.execution import execute_week
from test_accounting import sample_context
import test_pipeline


class FeeTests(unittest.TestCase):
    def test_user_rates_and_taxes(self):
        self.assertAlmostEqual(order_cost(10000,'BUY')['fee'],3.99)
        self.assertAlmostEqual(order_cost(10000,'SELL')['fee'],33.99)
        self.assertAlmostEqual(order_cost(10000,'SELL',instrument='etf')['fee'],13.99)
        self.assertAlmostEqual(order_cost(10000,'SELL',eligible_day_trade=True)['fee'],18.99)

    def test_minimum_one_dollar_and_zero_order(self):
        self.assertEqual(order_cost(100,'BUY'),dict(commission=1,tax=0,fee=1))
        self.assertEqual(order_cost(0,'BUY')['fee'],0)
        self.assertAlmostEqual(order_cost(100,'SELL')['fee'],1.3)

    def test_internal_passes_charge_one_minimum_per_order(self):
        costs=OrderFees(StrategyConfig())
        self.assertEqual(costs.charge('2330','BUY',100)['fee'],1)
        self.assertEqual(costs.charge('2330','BUY',200)['fee'],0)
        self.assertAlmostEqual(costs.charge('2330','BUY',9700)['fee'],2.99)
        self.assertAlmostEqual(costs.charge('2330','SELL',10000)['fee'],33.99)
        self.assertEqual(costs.charge('2409','BUY',100)['fee'],1)

    def test_invalid_costs_rejected(self):
        for amount in [-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):order_cost(amount,'BUY')
            with self.assertRaises(ValueError):OrderFees(StrategyConfig()).quote('x','BUY',amount)
        with self.assertRaises(ValueError):order_cost(100,'SELL',instrument='unknown')


class StockDeliveryTests(unittest.TestCase):
    def sample(self,known=True):
        ex=pd.Timestamp('2026-01-09');release=pd.Timestamp('2026-01-23')
        delivery=[dict(code='2400',ex_date=str(ex.date()),tradable_date=str(release.date()),
            announced_on='2026-01-05',source='https://www.twse.com.tw/example')] if known else []
        ledger=CorporateLedger({ex:{'2400':{'ratio':1.1,'kind':'STOCK_DIVIDEND'}}},delivery)
        pos={'2400':{'shares':100,'avg':110.,'pool':'A'}}
        cash,records=ledger.advance(ex,pos,1000,{'2400':100.})
        return ledger,pos,cash,ex,release,records

    def test_stock_rights_are_not_cash_and_not_sellable_on_ex_date(self):
        ledger,pos,cash,ex,release,records=self.sample()
        self.assertEqual(cash,1000)
        self.assertEqual(pos['2400']['shares'],110)
        self.assertEqual(pos['2400']['locked_shares'],10)
        self.assertEqual(ledger.value,0)
        pending=ledger.pending_assets(ex,pos,{'2400':100})
        self.assertEqual(pending[0]['book_value'],1000)
        self.assertEqual(pending[0]['tradable_date'],'2026-01-23')
        self.assertEqual(100*100+pending[0]['book_value'],11000)
        self.assertFalse(ledger.unlock(release-pd.Timedelta(days=1),pos))
        self.assertEqual(pos['2400']['locked_shares'],10)

    def test_only_verified_delivery_unlocks_without_cash_or_quantity_gain(self):
        ledger,pos,cash,ex,release,records=self.sample()
        unlocked=ledger.unlock(release,pos)
        self.assertEqual(pos['2400']['locked_shares'],0)
        self.assertEqual(pos['2400']['shares'],110)
        self.assertEqual(ledger.pending_assets(release,pos,{'2400':100}),[])
        self.assertEqual(unlocked[0]['side'],'SHARE_UNLOCK')
        self.assertFalse(ledger.unlock(release,pos))

    def test_unknown_date_stays_locked_and_is_visible(self):
        ledger,pos,cash,ex,release,records=self.sample(False)
        self.assertFalse(ledger.unlock(pd.Timestamp('2030-01-01'),pos))
        self.assertEqual(ledger.pending_assets(release,pos,{'2400':100})[0]['tradable_date'],None)
        self.assertEqual(pos['2400']['locked_shares'],10)

    def test_announcements_after_signal_cutoff_are_not_used(self):
        ledger,pos,cash,ex,release,records=self.sample()
        self.assertFalse(ledger.unlock(release,pos,known_through='2026-01-04'))
        self.assertIsNone(ledger.pending_assets(ex,pos,{'2400':100},known_through='2026-01-04')[0]['tradable_date'])

    def test_exit_sells_issued_shares_only_and_retains_pending_asset(self):
        ledger,pos,cash,ex,release,records=self.sample(False)
        ctx=sample_context();ctx.close.loc[:,:]=100.;ctx.valuation_close=ctx.close.copy()
        ctx.targetA.loc[:,:]=False;ctx.targetB.loc[:,:]=False
        ctx.receivable_total=1000.;ctx.share_entitlements=ledger.pending_assets(ex,pos,{'2400':100})
        flags=pd.DataFrame(False,index=ctx.days,columns=ctx.close.columns)
        cash,fills,plan=execute_week(ctx,pos,cash,ex,'close',(1.5,1.,.5),flags,flags)
        sales=[row for row in fills if row['side']=='SELL']
        self.assertEqual(sum(row['shares'] for row in sales),100)
        self.assertEqual(pos['2400']['shares'],10)
        self.assertEqual(pos['2400']['locked_shares'],10)
        self.assertEqual(plan['receivables'],1000)
        self.assertEqual(plan['stock_exposure'],0)
        self.assertAlmostEqual(cash,1000+10000-33.99)

    def test_future_cash_dividends_do_not_pay_on_unissued_rights(self):
        ledger,pos,cash,ex,release,records=self.sample(False)
        later=ex+pd.Timedelta(days=1)
        ledger.events[later]={'2400':{'cash':1.,'payment':later}}
        cash,records=ledger.advance(later,pos,cash,{'2400':100})
        self.assertEqual(cash,1100)

    def test_invalid_delivery_records_rejected(self):
        for row in [{},dict(code='x',ex_date='2026-01-09',tradable_date='2026-01-08',announced_on='2026-01-01',source='https://www.twse.com.tw/')]:
            with self.assertRaises(ValueError):delivery_registry([row])


class OfficialStatusTests(unittest.TestCase):
    def sample(self):
        return SimpleNamespace(config=StrategyConfig(),security_status={'checked_for':'2026-01-23',
            'listed':{'2400':{}},'events':[
            dict(code='2400',effective_date='2026-01-09',effective_time='090000',status='SUSPENDED'),
            dict(code='2400',effective_date='2026-01-12',effective_time='140000',status='TRADABLE')]})

    def test_suspend_and_intraday_restore_are_date_aware(self):
        ctx=self.sample()
        self.assertTrue(can_trade(ctx,'2400','2026-01-08'))
        self.assertFalse(can_trade(ctx,'2400','2026-01-09'))
        self.assertFalse(can_trade(ctx,'2400','2026-01-12'))
        self.assertTrue(can_trade(ctx,'2400','2026-01-13'))

    def test_current_universe_never_filters_earlier_years(self):
        ctx=self.sample()
        self.assertTrue(can_trade(ctx,'9998','2015-01-01'))
        self.assertFalse(can_trade(ctx,'9998','2026-01-23'))
        self.assertEqual(status_at(ctx,'9998','2026-01-23')['status'],'UNVERIFIED_LISTING')

    def test_positive_price_does_not_override_official_halt(self):
        ctx=sample_context();ctx.close.loc[:,:]=100.;ctx.valuation_close=ctx.close.copy()
        ctx.security_status=self.sample().security_status
        ctx.targetA.loc[:,:]=False;ctx.targetB.loc[:,:]=False;ctx.targetA['2400']=True
        pos={'2400':dict(shares=10,avg=100,pool='A')}
        flags=pd.DataFrame(False,index=ctx.days,columns=ctx.close.columns)
        cash,fills,plan=execute_week(ctx,pos,99000.,pd.Timestamp('2026-01-09'),'close',(1.5,1.,.5),flags,flags)
        self.assertEqual(fills,[])
        self.assertEqual(pos['2400']['shares'],10)
        self.assertEqual(plan['restricted_assets'][0]['code'],'2400')

    def test_delisted_zero_ends_only_on_verified_restoration(self):
        ctx=self.sample();ctx.security_status['events']=[dict(code='2400',effective_date='2026-01-09',status='DELISTED'),dict(code='2400',effective_date='2026-01-23',status='TRADABLE')]
        dates=pd.to_datetime(['2026-01-08','2026-01-09','2026-01-16','2026-01-23'])
        marked=prepare_valuation(ctx,pd.DataFrame({'2400':[100.,100.,100.,100.]},index=dates))
        self.assertEqual(marked['2400'].tolist(),[100.,0.,0.,100.])

    def test_incomplete_pagination_or_failed_response_rejected(self):
        with self.assertRaises(ValueError):table_rows({'stat':'ok','tables':[{'data':[[1]],'totalCount':2}]})
        with self.assertRaises(ValueError):table_rows({'stat':'failed','data':[]})

    def test_fetch_failure_does_not_overwrite_last_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'status.json';p.write_text('original',encoding='utf-8')
            def broken(url):raise OSError('official feed unavailable')
            with self.assertRaises(OSError):refresh_security_status(p,'2026-10-16','2026-10-15',getter=broken)
            self.assertEqual(p.read_text(encoding='utf-8'),'original')

    def test_read_only_load_does_not_replace_published_snapshot(self):
        snapshot=self.sample().security_status
        with patch('v5_engine.security_status.build_snapshot',return_value=snapshot),patch('v5_engine.jsonio.write_json') as write:
            result=refresh_security_status(None,'2026-10-16','2026-10-15',getter=lambda url:[])
            self.assertEqual(result,snapshot)
            write.assert_not_called()

    def test_roc_date_parser(self):
        for day in ['1151016','115/10/16','2026-10-16','20261016']:
            self.assertEqual(date_string(day),'2026-10-16')

    def payloads(self):
        return {'twse_listed':[dict(公司代號=str(1000+i),公司簡稱='Test',出表日期='1151016',上市日期='0900101') for i in range(500)],
            'tpex_listed':[dict(SecuritiesCompanyCode=str(5000+i),CompanyAbbreviation='Test',Date='1151016',DateOfListing='0900101') for i in range(300)],
            'twse_delisted':[],'twse_stopped':{'stat':'ok','data':[]},'twse_halts':[],'tpex_stopped':[],'tpex_halts':[]}

    def test_stale_or_partial_universe_stops_snapshot_build(self):
        payload=self.payloads();payload['twse_listed'][0]['出表日期']='1151015'
        with self.assertRaises(ValueError):build_snapshot(payload,'2026-10-16','2026-10-16')
        payload=self.payloads();payload['tpex_listed']=payload['tpex_listed'][:10]
        with self.assertRaises(ValueError):build_snapshot(payload,'2026-10-16','2026-10-16')

    def test_transfer_to_twse_is_not_delisting_loss(self):
        payload=self.payloads();payload['tpex_delisted_2026']={'stat':'ok','data':[['1000','Test','115/10/01','第12條之2第1項第1款']]}
        sources=dict(SOURCES,tpex_delisted_2026='https://www.tpex.org.tw/test')
        ctx=SimpleNamespace(security_status=build_snapshot(payload,'2026-10-16','2026-10-16',sources))
        self.assertTrue(can_trade(ctx,'1000','2026-10-01'))
        event=[row for row in ctx.security_status['events'] if row.get('label')=='轉上市'][0]
        self.assertEqual(event['status'],'TRADABLE')


class StatusGateTests(unittest.TestCase):
    status=test_pipeline.PublishTests.status
    signal=test_pipeline.PublishTests.signal
    evaluate=test_pipeline.PublishTests.evaluate
    def test_status_digest_mismatch_blocks_publication(self):
        signal=self.signal();signal['security_status_hash']='incorrect'
        self.assertFalse(self.evaluate(signal)['ok'])

    def test_missing_status_digest_blocks_publication(self):
        signal=self.signal();signal.pop('security_status_hash')
        self.assertFalse(self.evaluate(signal)['ok'])

    def test_pending_delivery_changes_notification_fingerprint(self):
        from publish_gate import fingerprint
        signal=self.signal();changed=copy.deepcopy(signal)
        changed['pending_assets']=[dict(code='2400',shares=10,ex_date='2026-10-01',tradable_date=None)]
        self.assertNotEqual(fingerprint(signal),fingerprint(changed))


if __name__=='__main__':unittest.main()
