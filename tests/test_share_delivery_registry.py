import json
from pathlib import Path
import unittest
from urllib.parse import urlparse
import pandas as pd
from v4_engine.corporate_actions import CorporateLedger, delivery_registry


class DeliveryBackfillTests(unittest.TestCase):
    def records(self):
        return json.loads((Path(__file__).resolve().parents[1]/'v4_engine/share_delivery_events.json').read_text(encoding='utf-8'))['events']

    def test_all_23_historical_entitlements_have_primary_evidence(self):
        records=self.records();registry=delivery_registry(records)
        self.assertEqual(len(records),len(registry))
        self.assertGreaterEqual(len(registry),23)
        for row in records:
            self.assertIn(urlparse(row['source']).hostname,{'www.twse.com.tw','www.tpex.org.tw','fsmarket.fbs.com.tw'})
            self.assertTrue(row.get('subject'))

    def test_right_certificates_use_first_tradable_date(self):
        records=delivery_registry(self.records())
        for code,ex,release in [('6506','2015-05-25','2015-06-18'),('1229','2021-09-24','2021-10-22'),('6446','2026-07-09','2026-08-06')]:
            row=records[(code,pd.Timestamp(ex))]
            self.assertEqual(row['security_form'],'RIGHT_CERTIFICATE')
            self.assertEqual(row['tradable_date'],pd.Timestamp(release))

    def test_issuer_confirmation_is_observed_only_on_delivery_day(self):
        record=next(r for r in self.records() if r['code']=='5820')
        self.assertNotIn('announced_on',record)
        ledger=CorporateLedger({},[record])
        ledger.pending=[dict(code='5820',shares=40,ex_date=pd.Timestamp(record['ex_date']))]
        pos={'5820':dict(shares=40,locked_shares=40,pool='A')}
        day=pd.Timestamp(record['tradable_date'])
        self.assertFalse(ledger.unlock(day,pos,known_through=day-pd.Timedelta(days=1)))
        self.assertEqual(pos['5820']['locked_shares'],40)
        self.assertEqual(len(ledger.unlock(day,pos,known_through=day)),1)
        self.assertEqual(pos['5820']['shares'],40)
        self.assertEqual(pos['5820']['locked_shares'],0)
        self.assertFalse(ledger.pending)
        self.assertFalse(ledger.unlock(day,pos))

    def test_confirmation_cannot_invent_earlier_knowledge(self):
        row=dict(next(r for r in self.records() if r['code']=='5820'))
        row['confirmed_on']='2018-09-13'
        with self.assertRaises(ValueError):delivery_registry([row])
        row['confirmed_on']='2018-09-14';row['announced_on']='2018-09-13'
        with self.assertRaises(ValueError):delivery_registry([row])


if __name__=='__main__':unittest.main()
