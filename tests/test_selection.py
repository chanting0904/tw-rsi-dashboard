from dataclasses import replace
from types import SimpleNamespace
import unittest

import pandas as pd

from v5_engine.config import StrategyConfig
from v5_engine.selection import select_universe


def context():
    days = pd.bdate_range('2026-10-12', periods=4)
    codes = [str(2000 + i) for i in range(25)]
    scores = pd.DataFrame([[100-i for i in range(25)]]*4, index=days, columns=codes)
    ctx = SimpleNamespace(config=StrategyConfig(), r20s=scores,
                          targetA=scores.notna(), targetB=scores < 0,
                          cap_ok=scores.notna(), tv20=scores*100,
                          tv20_previous=scores*100, sw_tsm={d: True for d in days})
    return ctx, days[-1], codes


class SelectionTests(unittest.TestCase):
    def test_new_entries_are_top15_and_no_extra_slots(self):
        ctx, day, codes = context()
        want, ranks, _, _, _ = select_universe(ctx, {}, day)
        self.assertEqual(want['A'], codes[:15])
        self.assertEqual(ranks, {c:i+1 for i,c in enumerate(codes[:15])})

    def test_existing_A_rank20_retained_and_rank21_exits(self):
        ctx, day, codes = context()
        positions = {codes[19]: {'pool':'A','shares':1}, codes[20]: {'pool':'A','shares':1}}
        want, ranks, _, _, _ = select_universe(ctx, positions, day)
        self.assertEqual(want['A'], codes[:14]+[codes[19]])
        self.assertEqual(ranks[codes[19]], 20)
        self.assertNotIn(codes[20], want['A'])

    def test_original_eligibility_and_capital_override_retention(self):
        for filter_name in ['targetA', 'cap_ok']:
            ctx, day, codes = context()
            getattr(ctx, filter_name).loc[day, codes[19]] = False
            want, _, _, _, _ = select_universe(ctx, {codes[19]: {'pool':'A','shares':1}}, day)
            self.assertNotIn(codes[19], want['A'])

    def test_only_existing_A_receives_buffer(self):
        ctx, day, codes = context()
        want, _, _, _, _ = select_universe(ctx, {codes[19]: {'pool':'B','shares':1}}, day)
        self.assertNotIn(codes[19], want['A'])

    def test_ranking_uses_three_shifted_sessions_not_last_score_only(self):
        ctx, day, codes = context()
        ctx.r20s.loc[:, codes[0]] = [99, 20, 20, 100]
        want, ranks, _, _, _ = select_universe(ctx, {}, day)
        self.assertNotIn(codes[0], want['A'])
        self.assertEqual(ranks[codes[1]], 1)
        # Appending arbitrary future observations cannot affect today's ranking.
        original = want
        ctx.r20s.loc[day+pd.Timedelta(days=3)] = 999
        self.assertEqual(select_universe(ctx, {}, day)[0], original)

    def test_legacy_B_candidates_cannot_split_the_single_strategy_budget(self):
        ctx, day, codes = context()
        ctx.targetB.loc[day, codes[:2]+codes[20:22]] = True
        ctx.tv20_previous.loc[day, codes[20]] = 1e9
        want, ranks, _, aw, bw = select_universe(ctx, {codes[0]:{'pool':'B','shares':1}}, day)
        self.assertEqual(want['B'], [])
        self.assertEqual(want['A'], codes[:15])
        self.assertNotIn(codes[20], ranks)
        self.assertEqual((aw, bw), (1., 0.))

    def test_legacy_market_switch_cannot_change_selection_or_allocation(self):
        ctx, day, _ = context()
        expected = select_universe(ctx, {}, day)
        for switch in (False, True):
            ctx.sw_tsm[day] = switch
            want, ranks, selected, aw, bw = select_universe(ctx, {}, day)
            self.assertEqual((want, ranks, selected, aw, bw), expected)
            self.assertEqual(want['B'], [])
            self.assertEqual((aw, bw), (1., 0.))

    def test_legacy_config_explicitly_restores_single_day_top15(self):
        ctx, day, codes = context()
        ctx.config = replace(ctx.config, a_rank_days=1, a_retention_rank=15)
        ctx.r20s.loc[day, codes[24]] = 101
        want, _, _, _, _ = select_universe(ctx, {codes[19]:{'pool':'A','shares':1}}, day)
        self.assertEqual(want['A'], [codes[24]]+codes[:14])


if __name__ == '__main__':
    unittest.main()
