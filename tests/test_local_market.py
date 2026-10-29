import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib
import hashlib
from unittest.mock import patch
from sync_market import update_financials

import pandas as pd

from sync_market import financial_effective_date, latest_quarter
from v5_engine.market_store import MarketStore, overlay
from v5_engine.official_sources import date_string, quarter_roe, quote_rows


class OfficialInputTests(unittest.TestCase):
    def test_current_quarter_refreshes_and_revisions_do_not_rewrite_old_observations(self):
        def html(kind, profit):
            fields = ['公司代號', '本期淨利（淨損）'] if kind == 'income' else ['公司代號', '權益總計', '股本']
            values = ['2330', str(profit)] if kind == 'income' else ['2330', '1000', '100']
            return ('<table><tr>'+''.join('<th>'+x+'</th>' for x in fields)+'</tr><tr>'+''.join('<td>'+x+'</td>' for x in values)+'</tr></table>').encode()
        with tempfile.TemporaryDirectory() as folder:
            store=MarketStore(folder)
            store._frames['price:收盤價']=pd.DataFrame({'2330':[1.]},index=pd.to_datetime(['2026-10-22']))
            store._frames['company_basic_info']=pd.DataFrame([{'stock_id':'2330','公司名稱':'測試公司'}])
            for market in ['sii','otc']:
                for quarter in [1,2]:
                    for kind in ['income','balance']:
                        store.save_document(f'financial:{market}:2026Q{quarter}:{kind}','2026-10-28T21:00:00+08:00','test',html(kind,100 if quarter==1 else 140))
            requests=[]
            def getter(url,params,post):
                requests.append(url)
                return html('income' if 't163sb04' in url else 'balance',150)
            def financial(name):
                return pd.DataFrame({'2330':[4. if name.startswith('fundamental') else 100.]},index=pd.to_datetime(['2026-08-14']))
            with patch.object(store,'financial',side_effect=financial):
                update_financials(store,folder,'2026-10-29','2026-10-29T21:00:00+08:00',getter,{})
                self.assertEqual(len(requests),4)
                self.assertEqual(store.db.execute("SELECT day,value FROM values_by_date WHERE dataset='fundamental_features:ROE稅後'").fetchone(),('2026-10-29',5.))
                requests.clear()
                update_financials(store,folder,'2026-10-29','2026-10-29T21:05:00+08:00',getter,{})
                self.assertEqual(requests,[])
            store.close()

    def test_historical_stock_name_labels_normalize_before_matching_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'local_db'
            path.mkdir()
            frame = pd.DataFrame({'date': pd.to_datetime(['2015-11-09']), '1527 鑽全': ['退還股款']})
            output = io.BytesIO()
            frame.to_feather(output)
            raw = output.getvalue()
            with sqlite3.connect(path / 'history_reference.sqlite3') as db:
                db.execute('CREATE TABLE frames(name TEXT PRIMARY KEY,sha TEXT,body BLOB)')
                db.execute('INSERT INTO frames VALUES(?,?,?)', ('capital_reduction_tse:減資原因', hashlib.sha256(raw).hexdigest(), zlib.compress(raw)))
            db.close()
            store = MarketStore(folder)
            self.assertEqual(store.base('capital_reduction_tse:減資原因').loc['2015-11-09', '1527'], '退還股款')
            store.close()

    def test_roe_uses_single_quarter_and_average_total_equity(self):
        self.assertEqual(quarter_roe(150, 100, 1100, 900, 2), 5)
        self.assertEqual(quarter_roe(100, None, 1100, 900, 1), 10)
        self.assertIsNone(quarter_roe(100, None, 1100, 900, 2))
        self.assertIsNone(quarter_roe(100, 50, None, 900, 2))
        self.assertIsNone(quarter_roe(100, 50, 0, 0, 2))

    def test_roe_sign_can_change_when_cumulative_profit_is_positive(self):
        self.assertLess(quarter_roe(90, 100, 1000, 1000, 2), 0)

    def test_financial_calendars_preserve_insurance_bank_ky_and_holidays(self):
        ordinary = {'公司名稱': '一般股份有限公司'}
        insurance = {'公司名稱': '測試人壽保險股份有限公司', '產業類別': '金融保險業'}
        bank = {'公司名稱': '測試銀行', '產業類別': '金融保險業'}
        ky = {'公司簡稱': '測試-KY'}
        self.assertEqual(financial_effective_date(ordinary, 2026, 2, set()), dt.date(2026, 8, 14))
        self.assertEqual(financial_effective_date(bank, 2026, 2, set()), dt.date(2026, 8, 31))
        self.assertEqual(financial_effective_date(ky, 2026, 2, set()), dt.date(2026, 8, 31))
        self.assertEqual(financial_effective_date(insurance, 2026, 3, set()), dt.date(2026, 11, 2))
        self.assertEqual(financial_effective_date(ordinary, 2026, 3, set()), dt.date(2026, 11, 16))
        self.assertEqual(latest_quarter(dt.date(2026, 10, 29)), (2026, 2))

    def test_dates_accept_roc_formats_but_not_invalid_days(self):
        for value in ['115/10/29', '1151029', '115年10月29日', '2026-10-29']:
            self.assertEqual(date_string(value), '2026-10-29')
        with self.assertRaises(ValueError):
            date_string('115/02/30')

    def test_overlay_handles_new_text_columns_without_changing_history(self):
        frame = pd.DataFrame({'2330': ['息']}, index=pd.to_datetime(['2026-10-22']))
        result = overlay(frame, [('2026-10-29', '2466', '權')])
        self.assertEqual(result.loc['2026-10-22', '2330'], '息')
        self.assertEqual(result.loc['2026-10-29', '2466'], '權')
        self.assertEqual(frame.shape, (1, 1))

    def test_adjustment_applies_ex_date_on_closed_day_at_next_actual_session(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(folder)
            raw = pd.DataFrame({'2330': [100.]}, index=pd.to_datetime(['2026-10-22']))
            store._frames['price:收盤價'] = raw
            store._frames['etl:adj_close'] = raw * 2
            store.db.execute('INSERT INTO bars VALUES(?,?,?,?,?,?,?,?)', ('2026-10-27', '2330', 90, 90, 90, 90, 1000, 'test'))
            store.db.execute('INSERT INTO references_by_date VALUES(?,?,?,?,?)', ('2026-10-23', '2330', 100, 90, 'test'))
            result = store.adjusted()
            self.assertAlmostEqual(result.loc['2026-10-27', '2330'], 200)
            self.assertEqual(result.loc['2026-10-22', '2330'], 200)
            store.close()

    def test_official_quotes_reject_wrong_date_partial_data_and_malformed_ohlc(self):
        row = ['2330', 'test', '100', '105', '95', '102', '10000']
        fields = ['證券代號', '證券名稱', '開盤價', '最高價', '最低價', '收盤價', '成交金額']
        payload = {'stat': 'OK', 'date': '20261029', 'tables': [{'fields': fields, 'data': [row]}]}
        with self.assertRaises(ValueError):
            quote_rows(payload, 'TWSE', '2026-10-28')
        with self.assertRaises(ValueError):
            quote_rows(payload, 'TWSE', '2026-10-29')
        row[3] = '90'
        with self.assertRaisesRegex(ValueError, '開高低收'):
            quote_rows(payload, 'TWSE', '2026-10-29')

    def test_database_transaction_rolls_back_a_partial_source_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(folder)
            with self.assertRaises(RuntimeError):
                with store.db:
                    store.db.execute('INSERT INTO bars VALUES(?,?,?,?,?,?,?,?)', ('2026-10-29', '2330', 10, 10, 10, 10, 1000, 'test'))
                    raise RuntimeError('second exchange missing')
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM bars').fetchone()[0], 0)
            store.close()
