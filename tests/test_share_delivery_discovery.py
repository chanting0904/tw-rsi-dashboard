import tempfile
import unittest
from pathlib import Path
import pandas as pd
from v4_engine.share_delivery import parse_announcements,match_deliveries,resolve_deliveries,merge_registered
from v4_engine.order_quotes import refresh_order_quotes,build_order_quotes

def payload(trade='115年08月25日',subject='公司115年除權配股股票上市掛牌日期。',issued='100',after='1,100'):
 return {'stat':'OK','data':[['2400','公司','(股票上市)增資新股上市',1,subject,'115年08月20日',trade,issued,after,'含員工酬勞轉增資 0 股']]}

class DeliveryDiscoveryTests(unittest.TestCase):
 def events(self):return {pd.Timestamp('2026-08-01'):{'2400':dict(kind='STOCK_DIVIDEND',ratio=1.1)}}
 def parse(self,p=None,cutoff='2026-08-21'):return parse_announcements(p or payload(),'twse','https://www.twse.com.tw/test',cutoff)
 def test_only_published_dates_are_available(self):
  self.assertEqual(self.parse(cutoff='2026-08-19'),[])
  self.assertEqual(self.parse()[0]['tradable_date'],'2026-08-25')
 def test_stock_ratio_must_match(self):
  records,added,conflicts=match_deliveries(self.events(),self.parse(),[],'2026-08-21')
  self.assertEqual(len(records),1);self.assertEqual(len(added),1)
  self.assertFalse(match_deliveries(self.events(),self.parse(payload(issued='200')),[],'2026-08-21')[0])
 def test_employee_or_cash_issue_is_not_mistaken_for_bonus(self):
  self.assertEqual(self.parse(payload(subject='公司現金增資股票上市掛牌日期。')),[])
  p=payload();p['data'][0][9]='含員工酬勞轉增資 100 股';self.assertEqual(self.parse(p),[])
 def test_right_certificate_uses_first_tradable_date(self):
  p=payload(trade='115年08月22日',subject='公司115年新股權利證書上市開始買賣日期。');p['data'][0][2]='新股權利證書上市'
  rows=self.parse()+self.parse(p)
  records,_,_=match_deliveries(self.events(),rows,[],'2026-08-21')
  self.assertEqual(records[0]['tradable_date'],'2026-08-22')
 def test_ambiguous_or_conflicting_schedule_is_locked(self):
  records,_,conflicts=match_deliveries(self.events(),self.parse()+self.parse(payload(trade='115年08月26日')),[],'2026-08-21')
  self.assertFalse(records);self.assertTrue(conflicts)
  original=dict(self.parse()[0],ex_date='2026-08-01');original.pop('issued');original.pop('ratio')
  records,_,conflicts=match_deliveries(self.events(),self.parse(payload(trade='115年08月26日')),[original],'2026-08-21')
  self.assertFalse(records);self.assertTrue(conflicts)
 def test_partial_pagination_or_failed_table_rejected(self):
  with self.assertRaises(ValueError):parse_announcements({'stat':'failed'},'twse','https://example.com','2026-08-21')
  with self.assertRaises(ValueError):parse_announcements({'stat':'ok','tables':[{'data':[],'totalCount':1}]},'tpex','https://example.com','2026-08-21')
 def test_source_failure_never_creates_delivery_date(self):
  def broken(url):raise OSError('unavailable')
  records,status=resolve_deliveries(self.events(),[],'2026-08-21',getter=broken)
  self.assertFalse(records);self.assertEqual(status['status'],'PARTIAL')
 def test_successful_cache_is_reused_but_new_cutoff_refreshes(self):
  calls=[]
  def get(url):
   calls.append(url)
   return payload() if 'twse.com.tw' in url else {'stat':'ok','tables':[{'data':[],'totalCount':0}]}
  with tempfile.TemporaryDirectory() as d:
   a,s=resolve_deliveries(self.events(),[],'2026-08-21',getter=get,cache_dir=d)
   b,t=resolve_deliveries(self.events(),[],'2026-08-21',getter=get,cache_dir=d)
   self.assertEqual(a,b);self.assertEqual(len(calls),2);self.assertTrue(all(r['cached'] for r in t['sources']))
   resolve_deliveries(self.events(),[],'2026-08-22',getter=get,cache_dir=d);self.assertEqual(len(calls),4)
 def test_duplicate_saved_dates_merge_and_conflicts_fail(self):
  a=dict(code='2400',ex_date='2026-08-01',tradable_date='2026-08-25',announced_on='2026-08-20',source='https://www.twse.com.tw/test')
  self.assertEqual(len(merge_registered([a],[a])),1)
  with self.assertRaises(ValueError):merge_registered([a],[dict(a,tradable_date='2026-08-26')])

class OrderQuoteTests(unittest.TestCase):
 def test_old_quote_cannot_replace_new_or_ex_rights_reference(self):
  old={'2400':dict(price=100,date='2026-08-21',price_valid_from='2026-08-21')}
  self.assertEqual(refresh_order_quotes(old,{'2400':110},'2026-08-20')['2400']['price'],100)
  self.assertEqual(refresh_order_quotes(old,{'2400':120},'2026-08-22')['2400']['price'],120)
 def test_unknown_security_does_not_become_tradable(self):
  s=dict(checked_for='2026-08-21',listed={'2400':dict(name='公司')},events=[])
  q=build_order_quotes({'actions':[]},{'9999':100,'2400':50},'2026-08-21',s)
  self.assertNotIn('9999',q);self.assertEqual(q['2400']['price'],50);self.assertTrue(q['2400']['tradable'])

if __name__=='__main__':unittest.main()
