from dataclasses import replace
import unittest
import pandas as pd
from test_accounting import sample_context
from v4_engine.execution import execute_week
from v4_engine.allocation import preview_allocation
from v4_engine.sizing import proportional_quantities
from v4_engine.valuation import prepare_valuation
from v4_engine.simulation import run
from v4_engine.performance import perf_document, summarize_simulation


class AllocationRepairTests(unittest.TestCase):
    def test_chart_drawdown_includes_initial_capital(self):
        ctx=sample_context()
        capital=ctx.config.start_capital
        nav=[{'date':pd.Timestamp('2026-01-05'),'nav':capital*.99},
             {'date':pd.Timestamp('2026-01-06'),'nav':capital*.98}]
        result=summarize_simulation(nav,[],ctx.close,ctx.config,5,'close',(1.5,1.,.5))
        document=perf_document(result,ctx.config)
        self.assertAlmostEqual(result['mdd'],-.02)
        self.assertEqual(document['dd'][-1][1],-2.)

    def test_basket_sizing_is_independent_of_request_order(self):
        rows=[{'code':'c','price':110.,'quantity':100},{'code':'a','price':300.,'quantity':50},{'code':'b','price':20.,'quantity':500}]
        first=proportional_quantities(rows,18000.)
        self.assertEqual(first,proportional_quantities(list(reversed(rows)),18000.))
        self.assertLessEqual(sum(first[r['code']]*r['price'] for r in rows),18000.)

    def test_new_basket_is_funded_before_first_stock_is_bought(self):
        ctx=sample_context()
        ctx.close.loc[:,:]=100.
        ctx.open_=ctx.close.copy();ctx.adjusted_close=ctx.close.copy();ctx.valuation_close=ctx.close.copy()
        day=ctx.days[2]
        positions={str(2400+i):{'shares':160,'avg':100.,'pool':'A','wc':1.5,'entry_d':ctx.days[0]} for i in range(5)}
        flags=ctx.close<0
        cash,fills,plan=execute_week(ctx,positions,20000.,day,'close',(1.5,1.,.5),flags,flags)
        self.assertTrue(any(t['reason']=='PORTFOLIO_BAND_FUNDING' for t in fills))
        for row in plan['recs']:
            if row['code'] not in {str(2400+i) for i in range(5)}:
                self.assertGreaterEqual(row['cur_sh'],int(row['target_sh']*.90))
        self.assertGreaterEqual(plan['stock_exposure'],.90)
        self.assertLessEqual(plan['stock_exposure'],.95+1e-10)
        spent=sum(t['amount']*1.0004 for t in fills if t['side']=='BUY')
        received=sum(t['amount']*.997 for t in fills if t['side']=='SELL')
        self.assertAlmostEqual(cash,20000.+received-spent)

    def test_restricted_and_unaffordable_allocations_are_redistributed(self):
        rows=[{'code':'cheap','price':100.,'target_weight':.80},
              {'code':'costly','price':10000.,'target_weight':.10},
              {'code':'dead','price':81.,'target_weight':.05,'allocation_eligible':False}]
        result=preview_allocation(rows,100000.)
        self.assertEqual(result['quantities']['dead'],0)
        self.assertTrue(result['band_reachable'])
        self.assertAlmostEqual(result['cash']+result['invested']+result['fees'],100000.)

    def test_old_887_percent_case_now_reaches_the_band(self):
        rows=[{'code':'cheap','price':111.5,'target_weight':.4},
              {'code':'expensive','price':6605.,'target_weight':.53653}]
        result=preview_allocation(rows,100000.)
        self.assertGreaterEqual(result['exposure'],.90)
        self.assertLessEqual(result['exposure'],.95+1e-10)

    def test_unreachable_integer_band_is_reported_honestly(self):
        result=preview_allocation([{'code':'x','price':60000.,'target_weight':.95}],100000.)
        self.assertFalse(result['band_reachable'])
        self.assertEqual(result['quantities']['x'],1)
        self.assertGreater(result['below_band_amount'],0)

    def test_delisting_write_down_does_not_generate_sale_cash(self):
        ctx=sample_context()
        ctx.close=pd.DataFrame({'3089':[81.,float('nan'),float('nan')]},index=pd.to_datetime(['2024-03-01','2024-03-04','2024-03-05']))
        marks=prepare_valuation(ctx,ctx.close)
        self.assertEqual(marks.iloc[0,0],81.)
        self.assertEqual(marks.iloc[1,0],0.)
        ctx.config=replace(ctx.config,delisted_valuation='last_quote')
        self.assertEqual(prepare_valuation(ctx,ctx.close).iloc[1,0],81.)

    def test_daily_nav_detects_midweek_drawdown(self):
        ctx=sample_context()
        days=pd.to_datetime(['2026-01-02','2026-01-05','2026-01-06','2026-01-07','2026-01-08','2026-01-09'])
        for field in ['close','open_','high','low','targetA','targetB','r20s','tv20','ma60','cap_ok']:
            frame=getattr(ctx,field)
            setattr(ctx,field,pd.DataFrame([frame.iloc[0].to_dict()]*len(days),index=days))
        ctx.days=days[[0,-1]];ctx.days_all=days;ctx.weeks=ctx.days
        ctx.sw_tsm={day:True for day in days}
        ctx.close.loc[days[2],:]*=.8
        result=run(ctx)
        self.assertEqual(len(result['nav']),len(days))
        self.assertLess(result['mdd'],-.18)

    def test_start_date_excludes_prior_calendar_year_trade(self):
        ctx=sample_context()
        ctx.config=replace(ctx.config,start_date='2026-01-05')
        result=run(ctx)
        self.assertGreaterEqual(result['nav'].index[0],pd.Timestamp('2026-01-05'))
        self.assertTrue((result['trades_df'].date>=pd.Timestamp('2026-01-05')).all())


if __name__=='__main__':unittest.main()
