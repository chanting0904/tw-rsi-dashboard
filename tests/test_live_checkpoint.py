import copy
import json
import subprocess
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from test_accounting import sample_context
from v5_engine.live_model import load_checkpoint, check_predecessor, validate_published_carry
from v5_engine.jsonio import write_json, document_digest
from v5_engine.simulation import run_reference

ROOT = Path(__file__).resolve().parents[1]


class LiveCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.checkpoint = json.loads((ROOT/'output/live_checkpoints/2026-10-23.json').read_text(encoding='utf-8'))
        write_json(self.output/'live_checkpoints/2026-10-23.json', self.checkpoint)

    def test_exact_published_account_survives_changed_historical_prices(self):
        seed = load_checkpoint(ROOT, self.output, '2026-10-30')
        self.assertEqual(seed['positions']['2466']['shares'], 1885)
        self.assertEqual(seed['positions']['2884']['shares'], 4257)
        self.assertEqual(seed['positions']['6277']['shares'], 6323)
        self.assertNotIn('6221', seed['positions'])
        stocks = sum(p['shares']*seed['valuation_prices'][c] for c,p in seed['positions'].items())
        self.assertAlmostEqual(stocks+seed['cash']+sum(x['amount'] for x in seed['receivables']),seed['nav'])
        self.assertAlmostEqual(seed['cash'],590979.5986867308)

    def test_same_week_retries_never_seed_from_own_output(self):
        newer = copy.deepcopy(self.checkpoint)
        newer['date'] = '2026-10-30'
        write_json(self.output/'live_checkpoints/2026-10-30.json',newer)
        self.assertEqual(load_checkpoint(ROOT,self.output,'2026-10-30')['date'],'2026-10-23')
        with self.assertRaises(ValueError):
            load_checkpoint(ROOT,self.output,'2026-11-06')

    def test_damaged_or_missing_predecessor_is_not_silently_recomputed(self):
        previous = copy.deepcopy(self.checkpoint['previous_signal'])
        write_json(self.output/'previous_capital_signal.json', previous)
        check_predecessor(self.output,self.checkpoint,'2026-10-30')
        previous['actions'][0]['post_shares'] += 1
        write_json(self.output/'previous_capital_signal.json',previous)
        with self.assertRaises(ValueError):
            check_predecessor(self.output,self.checkpoint,'2026-10-30')
        self.checkpoint['cash'] += 1
        write_json(self.output/'live_checkpoints/2026-10-23.json',self.checkpoint)
        with self.assertRaises(ValueError):
            load_checkpoint(ROOT,self.output,'2026-10-30')

    def test_saved_valuation_supports_nontrading_seed_date(self):
        ctx = sample_context()
        seed = dict(date='2026-01-08',nav=100000.,cash=10000.,positions={
            '2400':dict(shares=900,avg=100.,pool='A',wc=1.,entry_d='2026-01-02')},
            pending=[],receivables=[],valuation_prices={'2400':100.})
        self.assertNotIn(pd.Timestamp(seed['date']),ctx.close.index)
        result = run_reference(ctx,seed=seed)
        self.assertEqual(result['nav'].iloc[0]['nav'],100000.)
        self.assertEqual(result['closing_state']['date'],'2026-01-23')

    def test_missing_disposition_fails_before_publishing_web_or_tg(self):
        previous = self.checkpoint['previous_signal']
        current = copy.deepcopy(previous)
        current['rebalance_date'] = '2026-10-30'
        for row in current['actions']:
            row['current_shares'] = row['post_shares']
        current['actions'] = [a for a in current['actions'] if a['stock_code'] != '2884']
        with self.assertRaisesRegex(ValueError,'2884'):
            validate_published_carry(previous,current)

    def test_released_two_capital_example_keeps_23_shares_and_complete_exits(self):
        current = json.loads((ROOT/'output/live_checkpoints/2026-10-30.json').read_text(encoding='utf-8'))['previous_signal']
        quotes = {a['stock_code']:dict(price=a['price'],tradable=a['trading_status'] in ('TRADABLE','OUTSIDE_TARGET'),
            buyable=a['buy_allowed'],sellable=a['sell_allowed']) for a in current['actions']}
        args = dict(previous=self.checkpoint['previous_signal'],current=current,previousCapital=100037,capital=100810,quotes=quotes)
        process = subprocess.run(['node',str(ROOT/'v5_engine/guarded_bridge.cjs')],input=json.dumps(args)+'\n',
            capture_output=True,text=True,encoding='utf-8',check=True)
        reply = json.loads(process.stdout)
        self.assertTrue(reply['ok'],reply)
        plan = reply['plan']; rows = {r['code']:r for r in plan['rows']}
        self.assertEqual((rows['2466']['current'],rows['2466']['target']),(23,23))
        self.assertEqual(rows['2033']['current'],125)
        self.assertEqual((rows['2884']['current'],rows['2884']['target']),(53,0))
        self.assertTrue(plan['withinBudget'])
        self.assertLessEqual(plan['exposure'],.95)
