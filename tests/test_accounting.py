import datetime
import json
import math
from pathlib import Path
import tempfile
import unittest
import pandas as pd
from v4_engine.config import StrategyConfig
from v4_engine.context import MarketContext
from v4_engine.jsonio import write_json
from v4_engine.performance import trade_cycles, profit_factor, annual_performance
from v4_engine.simulation import run
from v4_engine.allocation import preview_allocation
from v4_engine.execution import execute_week
from v4_engine.validation import validate_plans
from v4_engine.canonical import canonical_for


def sample_context(capital=100_000, no_targets=False):
    days = pd.to_datetime(['2026-01-02', '2026-01-09', '2026-01-16', '2026-01-23'])
    codes = [str(2400 + i) for i in range(10)]
    prices = [100., 250., 1000., 15., 8000., 500., 120., 1200., 50., 300.]
    close = pd.DataFrame([prices] * len(days), index=days, columns=codes)
    a = pd.DataFrame(False, index=days, columns=codes)
    b = a.copy()
    a.iloc[:, :6] = not no_targets
    b.iloc[:, 6:] = not no_targets
    return MarketContext(StrategyConfig(start_capital=capital), close, close, close, close,
                         a.astype(bool), b.astype(bool), close * 0 + 70, close * 0 + 1_000_000,
                         close * .9, close * 0 + True, {day: True for day in days}, days, days, days,
                         name_map={code: code for code in codes})


class AccountingTests(unittest.TestCase):
    def exposure_case(self, capital=100_000, shares=None, missing=False, down=False):
        ctx = sample_context(capital)
        ctx.targetA.loc[:, :] = False
        ctx.targetA.loc[:, ['2400', '2401']] = True
        ctx.targetB.loc[:, :] = False
        ctx.close.loc[:, :] = 100.
        ctx.open_ = ctx.close.copy()
        ctx.adjusted_close = ctx.close.copy()
        day = ctx.days[1]
        if down:
            ctx.close.loc[ctx.days[0], '2400'] = 112.
            ctx.adjusted_close = ctx.close.copy()
        if missing:
            ctx.close.loc[day, '2400'] = float('nan')
            ctx.adjusted_close = ctx.close.copy()
        ctx.valuation_close = ctx.close.ffill()
        quantity = shares if shares is not None else int(capital * .9 / 100)
        positions = {'2400': {'shares': quantity, 'avg': 100., 'pool': 'A', 'wc': 1.5, 'entry_d': ctx.days[0]}}
        flags = pd.DataFrame(False, index=ctx.days, columns=ctx.close.columns)
        downs = flags.copy()
        downs.loc[day, '2400'] = down
        cash, fills, plan = execute_week(ctx, positions, capital - quantity * 100., day, 'close', (1.5, 1., .5), flags, downs)
        return ctx, positions, cash, fills, plan

    def test_sale_released_cash_cannot_make_later_buys_exceed_cap(self):
        for capital in [100_000, 200_000, 1_000_000]:
            ctx, positions, cash, fills, plan = self.exposure_case(capital)
            self.assertLessEqual(plan['stock_exposure'], .95 + 1e-10)
            self.assertGreaterEqual(plan['stock_exposure'], .90)
            self.assertGreaterEqual(cash, 0)
            spent = sum(t['amount'] * 1.0004 for t in fills if t['side'] == 'BUY')
            received = sum(t['amount'] * .997 for t in fills if t['side'] == 'SELL')
            self.assertAlmostEqual(cash, capital * .1 + received - spent)
            self.assertEqual(plan['exposure_cap_blocked_codes'], [])

    def test_all_blocked_overweight_positions_do_not_fabricate_sales(self):
        for missing, down in [(False, True), (True, False)]:
            ctx, positions, cash, fills, plan = self.exposure_case(shares=970, missing=missing, down=down)
            self.assertEqual(positions['2400']['shares'], 970)
            self.assertFalse(any(t['side'] == 'BUY' for t in fills))
            self.assertEqual(plan['exposure_cap_blocked_codes'], ['2400'])
            frame = pd.DataFrame(fills, columns=['date', 'code', 'side', 'shares', 'amount'])
            checks, failures, warnings = validate_plans(ctx, [plan], frame)
            self.assertNotIn('exposure_upper_bound', failures)
            self.assertIn('exposure_upper_bound', warnings)
            signal = canonical_for(ctx, plan, frame, 'test1234')
            self.assertEqual(signal['exposure_cap_blocked_codes'], ['2400'])
            self.assertTrue(any('停止超額買進' in warning for warning in signal['warnings']))

    def test_unexplained_or_forged_overweight_plan_is_fatal(self):
        ctx, positions, cash, fills, plan = self.exposure_case()
        plan['stock_exposure'] = .97
        plan['exposure_cap_blocked_codes'] = ['2400']
        _, failures, _ = validate_plans(ctx, [plan], pd.DataFrame(fills))
        self.assertIn('exposure_upper_bound', failures)

    def test_empty_target_cash_and_large_receivables_never_force_buy(self):
        ctx = sample_context()
        ctx.receivable_total = 100_000
        ctx.valuation_close = ctx.close.ffill()
        flags = pd.DataFrame(False, index=ctx.days, columns=ctx.close.columns)
        positions = {}
        cash, fills, plan = execute_week(ctx, positions, 0., ctx.days[0], 'close', (1.5, 1., .5), flags, flags)
        self.assertEqual(cash, 0)
        self.assertEqual(positions, {})
        self.assertFalse(any(t['side'] == 'BUY' for t in fills))
        self.assertEqual(plan['receivables'], 100_000)
        self.assertEqual(plan['stock_exposure'], 0)

    def cycles(self, rows, buy_fee=0, sell_fee=0):
        dates = pd.date_range('2026-01-01', periods=len(rows))
        trades = pd.DataFrame([{'date': day, 'code': '2330', 'pool': 'A', 'rank': 1,
                               'side': side, 'shares': qty, 'price': price}
                              for day, (side, qty, price) in zip(dates, rows)])
        close = pd.DataFrame({'2330': [row[2] for row in rows]}, index=dates)
        return trade_cycles(trades, close, buy_fee, sell_fee)

    def test_earlier_partial_sale_counts_in_complete_cycle(self):
        cycles = self.cycles([('BUY', 100, 10), ('SELL', 50, 20), ('SELL', 50, 5)])
        self.assertEqual(cycles.loc[0, 'pnl'], 250)
        self.assertEqual(cycles.loc[0, 'ret'], .25)

    def test_addition_after_partial_sale_includes_all_fees(self):
        cycles = self.cycles([('BUY', 100, 10), ('SELL', 50, 20), ('BUY', 50, 12), ('SELL', 100, 8)], .0004, .003)
        self.assertAlmostEqual(cycles.loc[0, 'buy_cost'], 1600 * 1.0004)
        self.assertAlmostEqual(cycles.loc[0, 'sell_proceeds'], 1800 * .997)
        self.assertAlmostEqual(cycles.loc[0, 'pnl'], 1800 * .997 - 1600 * 1.0004)

    def test_open_position_not_counted_as_complete(self):
        self.assertTrue(self.cycles([('BUY', 100, 10), ('SELL', 50, 20)]).empty)

    def test_new_cycle_does_not_reuse_old_proceeds(self):
        cycles = self.cycles([('BUY', 100, 10), ('SELL', 100, 20), ('BUY', 50, 10), ('SELL', 50, 5)])
        self.assertEqual(cycles.pnl.tolist(), [1000, -250])

    def test_oversell_is_rejected(self):
        with self.assertRaises(ValueError):
            self.cycles([('BUY', 1, 10), ('SELL', 2, 10)])

    def test_zero_price_delisting_is_full_loss(self):
        cycles = self.cycles([('BUY', 1, 100), ('SELL', 1, 0)])
        self.assertEqual(cycles.loc[0, 'ret'], -1)

    def test_profit_factor_uses_money_not_sum_of_percentages(self):
        cycles = pd.DataFrame({'pnl': [100, -50], 'ret': [.1, -.5]})
        self.assertEqual(profit_factor(cycles), 2)

    def test_annual_return_includes_first_week(self):
        nav = pd.DataFrame({'nav': [120, 180, 150]}, index=pd.to_datetime(['2025-12-31', '2026-01-09', '2026-12-31']))
        years = annual_performance(nav, 100)
        self.assertAlmostEqual(years[1]['ret'], 25)
        self.assertAlmostEqual(math.prod(1 + row['ret'] / 100 for row in years), 1.5)
        self.assertEqual(years[1]['basis_date'], '2025-12-31')

    def test_annual_first_week_drawdown_and_one_point_year(self):
        nav = pd.DataFrame({'nav': [200, 100]}, index=pd.to_datetime(['2025-12-31', '2026-01-09']))
        year = annual_performance(nav, 100)[1]
        self.assertEqual(year['ret'], -50)
        self.assertEqual(year['mdd'], -50)

    def test_strict_json_has_no_nan_or_fake_999_profit_factor(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'perf.json'
            write_json(file, {'pf': float('inf'), 'value': float('nan')})
            self.assertEqual(json.loads(file.read_text(encoding='utf-8')), {'pf': None, 'value': None})

    def test_invalid_capital_rejected(self):
        for value in [0, -1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                StrategyConfig(start_capital=value)

    def test_three_capitals_have_integer_shares_cash_and_fee_reconciliation(self):
        for capital in [100_000, 200_000, 1_000_000]:
            result = run(sample_context(capital))
            for plan in result['weekly_plans']:
                self.assertGreaterEqual(plan['cash'], 0)
                self.assertAlmostEqual(plan['nav'], plan['cash'] + sum(row['cur_val'] for row in plan['recs']))
                self.assertTrue(all(int(row['cur_sh']) == row['cur_sh'] for row in plan['recs']))
            fills = result['trades_df']
            spend = float(fills.loc[fills.side == 'BUY', 'amount'].sum()) * 1.0004
            receipts = float(fills.loc[fills.side == 'SELL', 'amount'].sum()) * .997
            self.assertAlmostEqual(result['weekly_plans'][-1]['cash'], capital - spend + receipts)

    def test_all_cash_without_targets_does_not_crash(self):
        result = run(sample_context(no_targets=True))
        self.assertEqual(result['final'], 100_000)
        self.assertTrue(result['cycles'].empty)

    def test_capital_preview_includes_fee_and_rounding(self):
        for capital in [100_000, 200_000, 1_000_000]:
            result = preview_allocation([{'code': '2330', 'target_weight': .5, 'price': 1000},
                                         {'code': '3443', 'target_weight': .45, 'price': 8000}], capital)
            self.assertAlmostEqual(result['cash'] + result['invested'] + result['fees'], capital)
            self.assertGreaterEqual(result['cash'], 0)

    def test_preview_fee_never_overdraws_all_cash(self):
        result = preview_allocation([{'code': '2330', 'target_weight': 1, 'price': 100}], 1000)
        self.assertEqual(result['quantities']['2330'], 9)

    def test_preview_ceiling_accounts_for_fees_at_different_capitals(self):
        for capital in [100_000, 200_000, 1_000_000]:
            for weight in [.95, .95004, 1.]:
                result = preview_allocation([{'code': '2330', 'target_weight': weight, 'price': 1}], capital)
                self.assertLessEqual(result['exposure'], .95 + 1e-10)
                self.assertAlmostEqual(result['cash'] + result['invested'] + result['fees'], capital)

    def test_oversized_target_is_rejected(self):
        with self.assertRaises(ValueError):
            preview_allocation([{'code': '2330', 'target_weight': 1.1, 'price': 100}], 1000)

    def test_research_reports_use_shared_model_without_public_signal_writes(self):
        from v4_backtest import write_report
        context = sample_context()
        result = run(context)
        with tempfile.TemporaryDirectory() as folder:
            write_report(context, result, folder, folder)
            root = Path(folder)
            self.assertTrue((root / 'daily_trade_plan.csv').exists())
            self.assertEqual(json.loads((root / 'perf.json').read_text(encoding='utf-8'))['accounting_version'], 'v4_cash_entitlements_and_share_actions')
            self.assertFalse((root / 'latest_signals.json').exists())

    def test_importing_cli_does_not_load_market_or_write(self):
        import importlib
        import v4_canonical
        from unittest.mock import patch
        with patch('v4_engine.market.load_market') as load:
            importlib.reload(v4_canonical)
            load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
