import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import daily_update as daily

class Clock(dt.datetime):
    @classmethod
    def now(cls,tz=None):return cls(2026,10,23,6,tzinfo=tz)

class DailyFreshnessTests(unittest.TestCase):
    def run_daily(self,twse,tpex,finlab=None,*,write_error=False,prior_date=None):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);html=root/'index.html';original='const DATA = '+json.dumps({'holdings_a':[{'code':'2330','px':1.}],'holdings_b':[]})+';';html.write_text(original,encoding='utf-8')
            if prior_date:
                (root/'output').mkdir();(root/'output/prices.json').write_text(json.dumps({'px_date':prior_date,'prices':{'2330':10.}}),encoding='utf-8')
            def provider(value):return Mock(side_effect=value) if isinstance(value,Exception) else Mock(return_value=value)
            fin=provider(finlab or ({},None));get_twse,get_tpex=provider(twse),provider(tpex)
            with patch.multiple(daily,HERE=d,IDX=str(html),SRC=str(root/'missing.html')),patch.object(daily,'datetime',Clock),patch.dict(os.environ,{'FINLAB_TOKEN':'test-token' if finlab else ''}),patch.object(daily,'twse_prices',get_twse),patch.object(daily,'tpex_prices',get_tpex),patch.object(daily,'saved_prices',fin),patch.object(daily,'yf_prices',return_value=({},None)),contextlib.redirect_stdout(io.StringIO()):
                if write_error:
                    with patch.object(daily,'write_json',side_effect=OSError('injected disk failure')):
                        with self.assertRaises(OSError):daily.main()
                    self.assertEqual(html.read_text(encoding='utf-8'),original);return None
                try:daily.main()
                except RuntimeError:
                    self.assertEqual(html.read_text(encoding='utf-8'),original)
                    raise
            return json.loads((root/'output/prices.json').read_text(encoding='utf-8')),fin.call_count
    def test_fresh_official_prices_keep_priority(self):
        result,calls=self.run_daily(({'2330':100.},'20261022'),({'2317':20.},'115/10/22'))
        self.assertEqual(result['px_date'],'2026-10-22');self.assertEqual(result['prices'],{'2330':100.,'2317':20.});self.assertEqual(calls,0)
    def test_one_market_failure_uses_fallback_without_overwriting_official(self):
        result,calls=self.run_daily(({'2330':100.},'20261022'),RuntimeError('temporary outage'),({'2330':999.,'2317':20.},'20261022'))
        self.assertEqual(calls,1);self.assertEqual(result['prices'],{'2330':100.,'2317':20.})
    def test_stale_official_prices_do_not_suppress_fresh_fallback(self):
        result,calls=self.run_daily(({'2330':100.},'20261016'),({'2317':20.},'20261016'),({'2330':101.},'20261022'))
        self.assertEqual(calls,1);self.assertEqual(result['prices'],{'2330':101.})
    def test_all_stale_sources_fail_without_changing_html(self):
        with self.assertRaises(RuntimeError):self.run_daily(({'2330':100.},'20261016'),({'2317':20.},'20261016'))
    def test_write_failure_propagates_before_html_is_changed(self):
        self.run_daily(({'2330':100.},'20261022'),({'2317':20.},'20261022'),write_error=True)
    def test_older_price_cannot_roll_back_a_newer_file(self):
        with self.assertRaises(RuntimeError):self.run_daily(({'2330':100.},'20261022'),({'2317':20.},'20261022'),prior_date='2026-10-23')
    def test_future_or_undated_batches_and_invalid_prices_are_rejected(self):
        for stamp in [None,'20261026','20261016']:
            with self.assertRaises(ValueError):daily.checked_prices({'2330':100.},stamp,'2026-10-22','2026-10-23')
        with self.assertRaises(ValueError):daily.checked_prices({'2330':float('inf'),'2317':-1.},'20261022','2026-10-22','2026-10-23')
        self.assertEqual(daily.checked_prices({'2330':100.,'2317':float('nan')},'20261022','2026-10-22','2026-10-23')[0],{'2330':100.})
    def test_mixed_tpex_quote_dates_are_rejected(self):
        rows=[{'SecuritiesCompanyCode':'2400','Close':'10','Date':'1151021'},{'SecuritiesCompanyCode':'2401','Close':'20','Date':'1151022'}]
        with patch.object(daily,'_get',return_value=json.dumps(rows)),self.assertRaises(RuntimeError):daily.tpex_prices()
    def test_missing_tpex_quote_date_cannot_be_filled_by_another_stock(self):
        rows=[{'SecuritiesCompanyCode':'2400','Close':'10','Date':None},{'SecuritiesCompanyCode':'2401','Close':'20','Date':'1151022'}]
        with patch.object(daily,'_get',return_value=json.dumps(rows)),self.assertRaises(ValueError):daily.tpex_prices()

    def test_preopen_same_day_quote_is_not_a_completed_close(self):
        with self.assertRaises(RuntimeError):self.run_daily(({'2330':100.},'20261023'),({'2317':20.},'20261023'))
