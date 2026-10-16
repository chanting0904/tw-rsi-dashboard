import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import pandas as pd

from v4_engine.corporate_actions import CorporateLedger,build_events
from v4_engine.config import StrategyConfig
from v4_engine.context import MarketContext
from v4_engine.live_actions import prepare_live_actions
from v4_engine.simulation import run
from v4_engine.canonical import canonical_for
from v4_engine.execution import execute_week
from v4_engine.selection import select_universe
from v4_engine.validation import validate_plans
from unittest.mock import patch
from tg_push_v4 import build_message
from daily_update import update_file,read_data
from test_accounting import sample_context


def live_context(exit_original=True):
    dates=pd.bdate_range('2026-01-02','2026-01-23');ex=pd.Timestamp('2026-01-09')
    close=pd.DataFrame(100.,index=dates,columns=['2400','2401'])
    adjusted=close.copy();close.loc[close.index>ex,'2400']=98/1.1
    target=pd.DataFrame(False,index=dates,columns=close.columns)
    target.loc[:,'2400']=True
    if exit_original:
        target.loc[ex:,'2400']=False;target.loc[ex:,'2401']=True
    fridays=dates[dates.weekday==4]
    ctx=MarketContext(StrategyConfig(start_capital=10000),close,close.copy(),close.copy(),close.copy(),
        target,target&False,adjusted*0+75,adjusted*0+1e6,adjusted*.9,adjusted*0+True,
        {day:False for day in dates},fridays,dates,fridays,ex,ex-pd.Timedelta(days=1),{'2400':'原股','2401':'新股'})
    ctx.adjusted_close=adjusted
    ctx.corporate_events={ex:{'2400':{'cash':2.,'payment':pd.Timestamp('2026-01-23'),
        'ratio':1.1,'kind':'STOCK_DIVIDEND','cash_known_on':pd.Timestamp('2026-01-05'),
        'ratio_known_on':pd.Timestamp('2026-01-05')}}}
    ctx.share_delivery_events=[dict(code='2400',ex_date='2026-01-09',tradable_date='2026-01-23',
        announced_on='2026-01-05',source='https://www.twse.com.tw/test')]
    ctx.security_status={'checked_for':'2026-01-09','listed':{'2400':{},'2401':{}},'events':[]}
    return ctx


class ExRightsFlowTests(unittest.TestCase):
    def test_sell_original_on_ex_date_keeps_stock_and_cash_entitlements(self):
        ctx=live_context();result=run(ctx,end=ctx.live_day)
        old_buy=result['trades_df'].query("side=='BUY' and code=='2400'")['shares'].sum()
        sale=result['trades_df'].query("side=='SELL' and code=='2400'")
        self.assertEqual(int(sale['shares'].sum()),int(old_buy))
        self.assertAlmostEqual(sale.iloc[0]['price'],98/1.1)
        self.assertEqual(result['pending_assets'][0]['shares'],int(old_buy*.1+1e-8))
        self.assertAlmostEqual(result['receivables'][0]['amount'],old_buy*2)
        self.assertFalse(result['weekly_plans'][-1]['blocked_exit_codes'])
        self.assertNotIn('exposure_upper_bound',validate_plans(ctx,result['weekly_plans'],result['trades_df'])[1])

    def test_plan_and_completed_ex_date_have_same_accounting_without_market_move(self):
        live=live_context();planned=run(live,end=live.live_day)
        actual=live_context();actual.close.loc[actual.live_day,'2400']=98/1.1
        actual.live_day=None;actual.signal_day=None
        completed=run(actual,end='2026-01-09')
        self.assertAlmostEqual(planned['final'],completed['final'])
        self.assertEqual(planned['trades_df']['shares'].tolist(),completed['trades_df']['shares'].tolist())

    def test_no_phantom_profit_when_only_held_through_ex_date(self):
        ctx=live_context(False);result=run(ctx,end=ctx.live_day)
        fees=result['trades_df'].loc[result['trades_df']['date']==ctx.live_day,'fee'].fillna(0).sum()
        self.assertAlmostEqual(result['nav'].loc['2026-01-08','nav']-fees,result['nav'].loc['2026-01-09','nav'])

    def test_fully_sold_original_still_receives_new_shares_on_delivery(self):
        ctx=live_context();ctx.close.loc[ctx.live_day,'2400']=98/1.1
        ctx.live_day=None;ctx.signal_day=None
        result=run(ctx,end='2026-01-23')
        unlocks=result['trades_df'].query("side=='SHARE_UNLOCK' and code=='2400'")
        self.assertEqual(len(unlocks),1)
        self.assertEqual(str(unlocks.iloc[0]['date'].date()),'2026-01-23')
        sales=result['trades_df'].query("side=='SELL' and code=='2400'")
        old_buy=result['trades_df'].query("side=='BUY' and code=='2400'")['shares'].sum()
        self.assertEqual(int(sales['shares'].sum()),int(old_buy)+int(old_buy*.1+1e-8))
        self.assertEqual(result['pending_assets'],[])

    def test_unverified_ex_event_on_held_stock_stops_plan(self):
        ctx=live_context();ctx.corporate_events[ctx.live_day]['2400'].pop('ratio_known_on')
        with self.assertRaisesRegex(ValueError,'公告日期未核實'):run(ctx,end=ctx.live_day)

    def test_future_announcement_cannot_be_used_in_prior_close_plan(self):
        ctx=live_context();ctx.corporate_events[ctx.live_day]['2400']['cash_known_on']=ctx.live_day
        with self.assertRaisesRegex(ValueError,'公告日期未核實'):run(ctx,end=ctx.live_day)

    def test_reference_projection_is_repeatable_and_keeps_basis_close(self):
        ctx=live_context();prepare_live_actions(ctx);first=ctx.close.at[ctx.live_day,'2400']
        prepare_live_actions(ctx)
        self.assertEqual(first,ctx.close.at[ctx.live_day,'2400'])
        self.assertEqual(ctx.close.at[ctx.signal_day,'2400'],100)
        self.assertEqual(ctx.adjusted_close.at[ctx.live_day,'2400'],100)

    def test_new_stock_bought_on_ex_date_receives_no_same_day_grant(self):
        day=pd.Timestamp('2026-01-09');ledger=CorporateLedger({day:{'2400':dict(ratio=1.1,kind='STOCK_DIVIDEND')}})
        positions={};cash,records=ledger.advance(day,positions,1000,{'2400':100})
        positions['2400']={'shares':10,'pool':'A','avg':100.}
        ledger.advance(day+pd.Timedelta(days=1),positions,cash,{'2400':100})
        self.assertFalse(ledger.pending)
        self.assertEqual(positions['2400']['shares'],10)

    def test_sold_before_ex_date_receives_no_grant(self):
        day=pd.Timestamp('2026-01-09');ledger=CorporateLedger({day:{'2400':dict(ratio=1.1,kind='STOCK_DIVIDEND')}})
        cash,records=ledger.advance(day,{},1000,{'2400':100})
        self.assertEqual(records,[]);self.assertEqual(ledger.pending,[])

    def test_pending_only_position_can_reenter_as_new_purchase(self):
        ctx=sample_context();ctx.close.loc[:,:]=100.;ctx.valuation_close=ctx.close.copy()
        ctx.targetA.loc[:,:]=False;ctx.targetB.loc[:,:]=False;ctx.targetA['2400']=True
        ctx.receivable_total=1000.
        positions={'2400':dict(shares=10,locked_shares=10,avg=100,pool='A',wc=.5)}
        flags=pd.DataFrame(False,index=ctx.days,columns=ctx.close.columns)
        cash,fills,plan=execute_week(ctx,positions,99000.,ctx.days[1],'close',(1.5,1.,.5),flags,flags)
        buys=[t for t in fills if t['side']=='BUY']
        self.assertTrue(buys);self.assertTrue(buys[0]['is_new'])
        self.assertEqual(positions['2400']['wc'],1.5)
        self.assertEqual(positions['2400']['locked_shares'],10)

    def test_announcement_cutoff_uses_earlier_disclosure(self):
        dates=pd.to_datetime(['2026-01-08','2026-01-09']);raw=pd.DataFrame({'2400':[100.,100.]},index=dates)
        ann=pd.DataFrame([{'symbol':'2400','公告日期':pd.Timestamp(day),'除權交易日':dates[1],
            '盈餘轉增資配股(元/股)':amount,'普通股每股面額':'10'} for day,amount in [('2026-01-05',1),('2026-01-09',2)]])
        events=build_events(ann,raw,raw,as_of=dates[0])
        self.assertEqual(events[dates[1]]['2400']['ratio'],1.1)

    def test_daily_quote_cannot_overwrite_next_session_reference_price(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'index.html'
            data={'holdings_a':[dict(code='2400',px=98/1.1,px_date='2026-01-08',price_valid_from='2026-01-09',price_kind='EX_RIGHTS_REFERENCE')],'holdings_b':[]}
            file.write_text('const DATA = '+json.dumps(data)+';',encoding='utf-8')
            update_file(str(file),{'2400':100.},'2026-01-08','2026-01-09')
            _,parsed=read_data(file.read_text(encoding='utf-8'))
            self.assertAlmostEqual(parsed['holdings_a'][0]['px'],98/1.1)
            update_file(str(file),{'2400':91.},'2026-01-09','2026-01-09')
            _,parsed=read_data(file.read_text(encoding='utf-8'))
            self.assertEqual(parsed['holdings_a'][0]['px'],91.)
            self.assertEqual(parsed['holdings_a'][0]['price_kind'],'CLOSE')

    def test_web_and_telegram_show_original_sale_and_separate_pending_rights(self):
        ctx=live_context(False);result=run(ctx,end=ctx.live_day)
        signal=canonical_for(ctx,result['weekly_plans'][-1],result['trades_df'],'test')
        text=build_message(signal)
        self.assertIn('原有股票可照策略賣出',text)
        self.assertIn('2400',text)
        action=next(a for a in signal['actions'] if a['stock_code']=='2400')
        self.assertEqual(action['price_kind'],'EX_RIGHTS_REFERENCE')
        self.assertEqual(action['price_valid_from'],'2026-01-09')
        old_buy=result['trades_df'].query("side=='BUY' and code=='2400'").iloc[0]['shares']
        # A later small top-up may add issued shares, but pending rights never
        # enter the canonical target quantity before delivery.
        bought=int(result['trades_df'].query("side=='BUY' and code=='2400'")['shares'].sum())
        self.assertEqual(action['post_shares'],bought)
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory);(temp/'output').mkdir()
            for name in ['update_data.py','capital_preview.js','weekly_status.js']:
                (temp/name).write_bytes((root/name).read_bytes())
            (temp/'output/latest_signals.json').write_text(json.dumps(signal,ensure_ascii=False),encoding='utf-8')
            (temp/'output/prices.json').write_text(json.dumps({'px_date':'2026-01-08','prices':{'2400':100.}}),encoding='utf-8')
            env=dict(os.environ,V4_SKIP_LOCAL_UPDATE='1',V4_REVIEW_MODE='1',PYTHONPATH=str(root))
            process=subprocess.run([sys.executable,'-X','utf8',str(temp/'update_data.py')],env=env,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(process.returncode,0,process.stdout+process.stderr)
            _,data=read_data((temp/'index.html').read_text(encoding='utf-8'))
            self.assertAlmostEqual(data['holdings_a'][0]['px'],98/1.1,places=2)
            self.assertEqual(data['holdings_a'][0]['price_kind'],'EX_RIGHTS_REFERENCE')
            self.assertEqual(data['pending_assets'][0]['shares'],signal['pending_assets'][0]['shares'])

    def test_staged_pipeline_keeps_prior_close_cutoff_and_projects_ex_rights(self):
        from v4_engine.pipeline import publish
        from v4_engine.security_status import snapshot_hash
        ctx=live_context()
        with tempfile.TemporaryDirectory() as directory,patch('v4_engine.pipeline.fixed_anchor',return_value=({'date':'2026-01-02'},True)):
            result=publish(ctx,directory,Path(__file__).resolve().parents[1])
            signal=result['signal']
            self.assertEqual(signal['signal_mode'],'PRE_CLOSE_PLAN')
            self.assertEqual(signal['sig_date'],'2026-01-08')
            self.assertEqual(result['perf']['completed_through'],'2026-01-02')
            self.assertEqual(signal['security_status_hash'],snapshot_hash(json.loads((Path(directory)/'security_status.json').read_text(encoding='utf-8'))))
            sale=next(a for a in signal['actions'] if a['stock_code']=='2400')
            self.assertEqual(sale['action'],'SELL')
            self.assertGreater(sale['sell_shares'],0)
            self.assertTrue(signal['pending_assets'])


if __name__=='__main__':unittest.main()
