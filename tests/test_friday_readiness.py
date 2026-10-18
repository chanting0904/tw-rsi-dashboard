import copy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd

import test_pipeline as fixtures
import publish_gate as gate
from live_plan import prepare_prices
from publication_check import embedded_data
from v4_engine.jsonio import document_digest,validation_matches
from v4_engine.config import StrategyConfig
from v4_engine.context import MarketContext
from v4_engine.pipeline import publish
from version import RELEASE_VERSION
import recovery_check

ROOT=Path(__file__).resolve().parents[1]

class RequiredFeedTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({'2400':[100.,101.,102.,103.]},index=pd.bdate_range('2026-10-19','2026-10-22'))
    def test_each_required_signal_day_feed_must_be_present(self):
        for i in range(5):
            frames=[self.frame() for _ in range(5)];frames[i]=frames[i].iloc[:-1]
            with self.subTest(feed=i),self.assertRaises(ValueError):prepare_prices(*frames,'2026-10-23',set())
    def test_empty_or_nonfinite_signal_day_feed_is_rejected(self):
        for invalid in [float('nan'),float('inf'),0.,-1.]:
            for i in range(5):
                frames=[self.frame() for _ in range(5)];frames[i].iloc[-1]=invalid
                with self.subTest(feed=i,value=invalid),self.assertRaises(ValueError):prepare_prices(*frames,'2026-10-23',set())
    def test_duplicate_or_unsorted_dates_are_rejected(self):
        for frame in [pd.concat([self.frame(),self.frame().iloc[-1:]]),self.frame().iloc[::-1]]:
            with self.assertRaises(ValueError):prepare_prices(frame,*[self.frame() for _ in range(4)],'2026-10-23',set())
    def test_next_friday_sees_thursday_and_ignores_future_prices(self):
        source=self.frame();source.loc[pd.Timestamp('2026-10-23')]=9999.
        *frames,day,basis=prepare_prices(*[source]*5,'2026-10-23',set())
        self.assertEqual(str(day.date()),'2026-10-23');self.assertEqual(str(basis.date()),'2026-10-22')
        self.assertTrue(all(f.loc[day,'2400']==103. for f in frames));self.assertEqual(source.iloc[-1,0],9999.)

class BindingTests(unittest.TestCase):
    def test_old_pass_cannot_validate_altered_shares_cash_or_metadata(self):
        helper=fixtures.PublishTests();original=helper.signal()
        for field in ['cash','portfolio_value','generated_at']:
            changed=copy.deepcopy(original);changed[field]=0
            self.assertFalse(helper.evaluate(changed,validated_signal=original)['ok'])
        changed=copy.deepcopy(original);changed['actions'][0]['post_shares']+=1
        self.assertFalse(helper.evaluate(changed,validated_signal=original)['ok'])
    def test_old_pass_cannot_validate_a_different_performance_document(self):
        helper=fixtures.PublishTests();prior={'release_version':RELEASE_VERSION,'cagr':.4,'mdd':-.3,'completed_through':'2026-10-02'}
        changed=dict(prior,cagr=.41)
        self.assertFalse(helper.evaluate(helper.signal(),perf=changed,validated_perf=prior)['ok'])
    def test_digest_matches_json_roundtrip_and_covers_all_fields(self):
        doc={'cash':1.,'actions':[{'post_shares':2}],'date':dt.date(2026,10,23)}
        from v4_engine.jsonio import clean_json
        self.assertEqual(document_digest(doc),document_digest(json.loads(json.dumps(clean_json(doc)))))
        approved={'signal_digest':document_digest(doc)}
        self.assertTrue(validation_matches(doc,approved));self.assertFalse(validation_matches(dict(doc,cash=2.),approved));self.assertFalse(validation_matches(doc,{}))
    def test_rendered_web_never_marks_mismatched_validation_as_valid(self):
        signal=json.loads((ROOT/'output/latest_signals.json').read_text(encoding='utf-8'));signal['release_version']=RELEASE_VERSION
        perf=json.loads((ROOT/'output/perf.json').read_text(encoding='utf-8'));perf['release_version']=RELEASE_VERSION
        validation={'strategy_version':'V4','release_version':RELEASE_VERSION,'rebalance_date':signal['rebalance_date'],'verdict':'PASS','signal_digest':document_digest(signal),'performance_digest':document_digest(perf)}
        with tempfile.TemporaryDirectory() as d:
            tmp=Path(d);(tmp/'output').mkdir()
            for name in ['update_data.py','capital_preview.js','weekly_status.js','order_planner.js','order_planner_ui.js','weekly_capital.js','weekly_capital_ui.js','holidays.json']:(tmp/name).write_bytes((ROOT/name).read_bytes())
            for name,doc in [('latest_signals',signal),('perf',perf),('validation_log',validation)]: (tmp/'output'/(name+'.json')).write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
            env=dict(os.environ,V4_SKIP_LOCAL_UPDATE='1',PYTHONPATH=str(ROOT))
            def render():
                r=subprocess.run([sys.executable,'-X','utf8',str(tmp/'update_data.py')],env=env,capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(r.returncode,0,r.stderr);return embedded_data((tmp/'index.html').read_text(encoding='utf-8'))
            self.assertTrue(render()['sig_validated'])
            signal['cash']+=1;(tmp/'output/latest_signals.json').write_text(json.dumps(signal),encoding='utf-8')
            self.assertFalse(render()['sig_validated'])

class NextFridayPipelineTests(unittest.TestCase):
    def test_next_friday_pipeline_and_publication_gate_end_to_end(self):
        actual=pd.bdate_range('2026-10-12','2026-10-22');raw=pd.DataFrame(100.,index=actual,columns=['2400','2401','2402'])
        *frames,day,basis=prepare_prices(*[raw]*5,'2026-10-23',set())
        close,open_,high,low,turnover=frames;days=close.index;weeks=days[days.weekday==4]
        target=pd.DataFrame(False,index=days,columns=close.columns);target.loc[:,'2400']=True;target.loc[day,'2400']=False;target.loc[day,'2401']=True
        target_b=target&False;target_b.loc[:,'2402']=True
        cfg=StrategyConfig(start_date='2026-10-12',start_capital=100000)
        ctx=MarketContext(cfg,close,open_,high,low,target,target_b,close*0+75,close*0+1e6,close*.9,close*0+True,{d:True for d in days},weeks,days,weeks,day,basis,{c:c for c in close.columns})
        ctx.adjusted_close=close.copy();ctx.corporate_events={};ctx.share_delivery_events=[]
        ctx.security_status={'checked_for':'2026-10-23','quote_day':'2026-10-22','listed':{c:{} for c in close.columns},'events':[]}
        with tempfile.TemporaryDirectory() as d,patch('v4_engine.pipeline.fixed_anchor',return_value=({'date':'2026-10-16','cagr':0.,'mdd':0.},True)):
            output=publish(ctx,d,ROOT);signal=output['signal'];perf=output['perf'];validation=output['validation']
            self.assertEqual(signal['signal_mode'],'PRE_CLOSE_PLAN');self.assertEqual(signal['sig_date'],'2026-10-22');self.assertEqual(signal['price_basis_date'],'2026-10-22');self.assertEqual(signal['rebalance_date'],'2026-10-23')
            self.assertEqual(perf['completed_through'],'2026-10-16');self.assertTrue(validation_matches(signal,validation,perf))
            self.assertTrue(gate.evaluate(today=dt.date(2026,10,23),output_dir=d,notify=False)['ok'])
            self.assertGreaterEqual(signal['cash'],0);self.assertLessEqual(signal['stock_exposure'],.95)
            self.assertFalse(gate.evaluate(today=dt.date(2026,10,30),output_dir=d,notify=False)['ok'])

class RecoveryTests(unittest.TestCase):
    def mode(self,gate_ok=True,status_ready=True,delivered=True,public_ok=True,today=dt.date(2026,10,23)):
        signal={'rebalance_date':'2026-10-23','actions':[]};key=gate.fingerprint(signal)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'output').mkdir();(root/'output/latest_signals.json').write_text(json.dumps(signal),encoding='utf-8')
            (root/'output/weekly_status.json').write_text(json.dumps({'state':'ready' if status_ready else 'failed','rebalance_date':'2026-10-23','canonical_hash':key}),encoding='utf-8')
            (root/'state.json').write_text(json.dumps({'last_pushed_key':key if delivered else 'old'}),encoding='utf-8')
            with patch.object(recovery_check,'ROOT',root),patch.object(recovery_check,'evaluate',return_value={'ok':gate_ok}),patch.object(recovery_check,'verify_public_index',side_effect=None if public_ok else RuntimeError('offline failure')):
                return recovery_check.recovery_mode(today)[0]
    def test_complete_week_is_not_repeated(self):self.assertEqual(self.mode(),'skip')
    def test_missing_weekly_signal_triggers_full_recovery(self):self.assertEqual(self.mode(gate_ok=False),'full')
    def test_failed_ready_state_triggers_publication_recovery(self):self.assertEqual(self.mode(status_ready=False),'publish')
    def test_missing_delivery_or_publication_only_retries_unfinished_stage(self):
        self.assertEqual(self.mode(delivered=False),'publish');self.assertEqual(self.mode(public_ok=False),'publish')
    def test_ordinary_thursday_does_not_run_weekly_rebalance(self):self.assertEqual(self.mode(today=dt.date(2026,10,22)),'skip')

class RemainderCalendarTests(unittest.TestCase):
    def test_october_23_remainder_skips_october_26_official_holiday(self):
        holidays=gate.load_holidays();day=dt.date(2026,10,24)
        while not gate.is_trading(day,holidays):day+=dt.timedelta(days=1)
        self.assertEqual(day,dt.date(2026,10,27))

class ResearchIsolationTests(unittest.TestCase):
    def test_report_only_default_destination_is_not_public_output(self):
        import v4_canonical
        with patch('v4_engine.market.load_market',return_value=object()),patch('v4_engine.pipeline.publish') as publish_mock,patch.dict(os.environ,{'V4_OUTPUT_DIR':''}):
            self.assertEqual(v4_canonical.main(['--report-only','--capital','150000']),0)
            destination=Path(publish_mock.call_args.args[1]);self.assertEqual(destination.parent.name,'audit_reports');self.assertNotEqual(destination,ROOT/'output')
    def test_report_only_rejects_public_output_before_loading_market(self):
        import v4_canonical
        with patch('v4_engine.market.load_market') as load,patch.dict(os.environ,{'V4_OUTPUT_DIR':str(ROOT/'output')}):
            self.assertEqual(v4_canonical.main(['--report-only']),1);load.assert_not_called()
    def test_research_writer_cannot_overwrite_public_performance(self):
        from v4_backtest import write_report
        with self.assertRaises(ValueError):write_report(None,None,ROOT/'output',ROOT)

class PreviewRangeTests(unittest.TestCase):
    def test_oversized_capital_and_quantities_fail_instead_of_looping(self):
        from v4_engine.allocation import preview_allocation
        with self.assertRaises(ValueError):preview_allocation([{'code':'2330','target_weight':.95,'price':.01}],1e100)
        with self.assertRaises(ValueError):preview_allocation([{'code':'2330','target_weight':.95,'price':1e-10}],1e12)
