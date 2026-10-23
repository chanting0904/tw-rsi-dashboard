import unittest
from test_accounting import sample_context
from v5_engine.simulation import run
from v5_engine.canonical import canonical_for

class GuardedModelTests(unittest.TestCase):
    def test_retained_stock_does_not_create_new_buy_limit_warning(self):
        ctx=sample_context();result=run(ctx);plan=result['weekly_plans'][-1]
        plan['blocked_limitup_targets']=1
        plan['reference_blocked_limitup_targets']=0
        signal=canonical_for(ctx,plan,result['trades_df'],'test')
        self.assertFalse(any('新目標股漲停' in w for w in signal['warnings']))
        self.assertEqual(signal['execution_policy'],'guarded_weekly_capital_v1')
