import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from version import RELEASE_VERSION
from unittest.mock import patch

import pandas as pd
import publish_gate as gate
from live_plan import prepare_prices, completed_week_days
from market_dates import quote_date
from tg_push_v5 import build_message, message_chunks
from s5_live import recalc_weekly as recalc
from local_db_updater import merge_amt
import pages_publish


class CalendarTests(unittest.TestCase):
    def test_midweek_holiday_does_not_end_week(self):
        self.assertEqual(gate.last_trading_day_of_week(dt.date(2026, 10, 13), {"2026-10-14"}), dt.date(2026, 10, 16))

    def test_friday_holiday_moves_to_thursday(self):
        self.assertEqual(gate.last_trading_day_of_week(dt.date(2026, 10, 8), {"2026-10-09"}), dt.date(2026, 10, 8))

    def test_closed_week_has_no_rebalance(self):
        holidays = {f"2026-10-{day:02}" for day in range(12, 17)}
        self.assertIsNone(gate.last_trading_day_of_week(dt.date(2026, 10, 14), holidays))


class PagesTests(unittest.TestCase):
    def test_old_deployed_commit_does_not_count_as_new_build(self):
        replies = [{"status": "queued"}, {"status": "built", "commit": "old"}, {"status": "built", "commit": "new"}]
        with patch.object(pages_publish, "request_json", side_effect=replies), patch.object(pages_publish.time, "sleep"):
            self.assertEqual(pages_publish.publish("unused", "owner/repo", "new", attempts=2)["commit"], "new")

    def test_failed_pages_build_stops_notification_pipeline(self):
        replies = [{"status": "queued"}, {"status": "errored", "commit": "new", "error": {"message": "build failed"}}]
        with patch.object(pages_publish, "request_json", side_effect=replies):
            with self.assertRaises(RuntimeError):
                pages_publish.publish("unused", "owner/repo", "new", attempts=1)


class LivePlanTests(unittest.TestCase):
    def test_incomplete_week_is_not_a_completed_backtest_week(self):
        index = pd.to_datetime(["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"])
        ends = pd.to_datetime(["2026-10-02", "2026-10-09"])
        self.assertEqual(list(completed_week_days(index, ends, {"2026-10-09"})), [pd.Timestamp("2026-10-02")])

    def test_fully_closed_week_does_not_repeat_previous_session(self):
        index = pd.to_datetime(["2026-02-11", "2026-02-23"])
        ends = pd.to_datetime(["2026-02-13", "2026-02-20", "2026-02-27"])
        holidays = {"2026-02-12", "2026-02-13", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-27"}
        self.assertEqual(list(completed_week_days(index, ends, holidays)), [pd.Timestamp("2026-02-11")])

    def frame(self, end="2026-10-07"):
        index = pd.date_range("2026-10-05", end, freq="B")
        return pd.DataFrame({"2330": range(100, 100 + len(index))}, index=index)

    def test_plan_uses_previous_close_without_changing_source(self):
        source = self.frame()
        before = source.copy()
        *frames, day, signal_day = prepare_prices(source, source, source, source, source, "2026-10-08", {"2026-10-09"})
        self.assertEqual(signal_day, pd.Timestamp("2026-10-07"))
        self.assertEqual(frames[0].loc[day, "2330"], source.loc[signal_day, "2330"])
        self.assertEqual(frames[0].shift(1).loc[day, "2330"], source.loc[signal_day, "2330"])
        pd.testing.assert_frame_equal(source, before)

    def test_stale_quotes_are_rejected(self):
        source = self.frame("2026-10-06")
        with self.assertRaises(ValueError):
            prepare_prices(source, source, source, source, source, "2026-10-08", {"2026-10-09"})

    def test_nonweekly_execution_date_is_rejected(self):
        source = self.frame()
        with self.assertRaises(ValueError):
            prepare_prices(source, source, source, source, source, "2026-10-07", {"2026-10-09"})


class PublishTests(unittest.TestCase):
    def status(self):
        return {'checked_for':'2026-10-08','listed':{'2330':{'name':'A&B','exchange':'TWSE'}},'events':[]}

    def signal(self):
        return {"security_status_checked_for":"2026-10-08", "security_status_hash": __import__('v5_engine.security_status',fromlist=['snapshot_hash']).snapshot_hash(self.status()),
                "strategy_version": "V5", "release_version": RELEASE_VERSION, "rebalance_date": "2026-10-08", "sig_date": "2026-10-07",
                "price_basis_date": "2026-10-07", "signal_mode": "PRE_CLOSE_PLAN",
                "generated_at": "2026-10-08T06:00:00+08:00", "commit_sha": "test1234",
                "cash": 7500, "portfolio_value": 100000, "buffer": .075, "stock_exposure": .925,
                "counts": {"HOLD": 1}, "actions": [{"stock_code": "2330", "stock_name": "A&B",
                "channel": "A", "rank": 1, "action": "HOLD", "price": 100,
                "target_weight": .925, "current_weight": .925, "current_shares": 925,
                "post_shares": 925, "target_shares": 925, "buy_shares": 0, "sell_shares": 0}]}

    def evaluate(self, signal, perf=None, baseline=None, validated_signal=None, validated_perf=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'security_status.json').write_text(json.dumps(self.status()),encoding='utf-8')
            (root / "latest_signals.json").write_text(json.dumps(signal), encoding="utf-8")

            performance = dict(perf or {"cagr": .3869, "mdd": -.3564, "completed_through": "2026-10-02"})
            performance.setdefault('release_version', RELEASE_VERSION)
            from v5_engine.jsonio import document_digest
            validation={'strategy_version':'V5','release_version':RELEASE_VERSION,'verdict':'PASS','rebalance_date':signal['rebalance_date'],
                'signal_digest':document_digest(validated_signal or signal),'performance_digest':document_digest(validated_perf or performance)}
            (root/'validation_log.json').write_text(json.dumps(validation),encoding='utf-8')
            (root / "perf.json").write_text(json.dumps(performance), encoding="utf-8")
            if baseline:
                (root / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
            return gate.evaluate(today=dt.date(2026, 10, 8), output_dir=directory, notify=False)

    def test_valid_prior_close_plan_is_publishable(self):
        self.assertTrue(self.evaluate(self.signal())["ok"])

    def test_new_week_returns_do_not_trigger_fixed_anchor_drift(self):
        perf = {"cagr": .8, "mdd": -.2, "completed_through": "2026-10-02",
                "fixed_anchor": {"date": "2026-10-02", "cagr": .3785, "mdd": -.3564}}
        baseline = {"cagr": 37.85, "mdd": -35.64, "anchor_date": "2026-10-02"}
        self.assertTrue(self.evaluate(self.signal(), perf, baseline)["ok"])

    def test_force_cannot_publish_old_week(self):
        signal = self.signal()
        signal["rebalance_date"] = "2026-10-02"
        self.assertFalse(self.evaluate(signal)["ok"])

    def test_same_total_but_wrong_action_counts_are_rejected(self):
        signal = self.signal()
        signal["counts"] = {"BUY": 1}
        self.assertFalse(self.evaluate(signal)["ok"])

    def test_nan_weight_is_rejected(self):
        signal = self.signal()
        signal["actions"][0]["target_weight"] = float("nan")
        self.assertFalse(self.evaluate(signal)["ok"])

    def test_unexplained_exposure_over95_is_rejected_even_with_small_weight_sum(self):
        signal = self.signal()
        signal['stock_exposure'] = .96
        self.assertFalse(self.evaluate(signal)['ok'])

    def test_declaring_blocked_codes_alone_cannot_bypass_exposure_cap(self):
        signal = self.signal()
        signal['stock_exposure'] = .97
        signal['exposure_cap_blocked_codes'] = ['2330']
        self.assertFalse(self.evaluate(signal)['ok'])

    def test_verified_block_must_cover_all_held_positions_and_match_exposure(self):
        signal = self.signal()
        signal.update(stock_exposure=.97, exposure_cap_blocked_codes=['2330'])
        signal['actions'][0]['post_shares'] = 970
        validation = {'checks': {'exposure_upper_bound': {'detail': [{
            'date': signal['rebalance_date'], 'justified': True, 'blocked_codes': ['2330'], 'exposure': .97}]}}}
        self.assertTrue(gate.documented_upper_trade_block(signal, validation))
        signal['actions'].append({'stock_code': '2409', 'post_shares': 1})
        self.assertFalse(gate.documented_upper_trade_block(signal, validation))

    def test_changed_quantity_changes_notification_key(self):
        signal = self.signal()
        changed = copy.deepcopy(signal)
        changed["actions"][0]["buy_shares"] = 3
        self.assertNotEqual(gate.fingerprint(signal), gate.fingerprint(changed))

    def test_telegram_has_no_fabricated_account_holdings(self):
        message = build_message(self.signal())
        self.assertIn("A&amp;B", message)
        self.assertIn("92.5%", message)
        self.assertNotIn("本金固定", message)
        self.assertNotIn("目前 925", message)

    def test_long_telegram_is_split_under_limit(self):
        text = "\n".join(["<b>訊號</b>" * 20] * 50)
        chunks = list(message_chunks(text))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 3500 for chunk in chunks))


class PriceDateTests(unittest.TestCase):
    def test_same_day_missing_market_can_be_repaired(self):
        frame = pd.DataFrame({"date": [pd.Timestamp("2026-10-07")], "2330": [100.0], "6199": [float("nan")]})
        with patch("local_db_updater.pd.read_feather", return_value=frame):
            changed, result = merge_amt("unused.feather", "2026-10-07", {"6199": {"px": 132}, "9999": {"px": 10}}, "px")
        self.assertTrue(changed)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.loc[0, "2330"], 100)
        self.assertEqual(result.loc[0, "6199"], 132)
        self.assertEqual(result.loc[0, "9999"], 10)

    def test_official_formats(self):
        for value in ("1151007", "115/10/07", "20261007", "2026-10-07"):
            self.assertEqual(quote_date(value), "2026-10-07")

    def test_missing_date_is_not_fabricated(self):
        with self.assertRaises(ValueError):
            quote_date(None)


class ExecutionReportTests(unittest.TestCase):
    def test_sell_fills_are_not_counted_as_remaining_stock_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orders = root / "orders"
            orders.mkdir()
            pd.DataFrame([{"week": "2026-W41", "execution_date": "2026-10-08", "pool": "A",
                           "side": "SELL", "symbol": "2330 A", "target_qty": 20,
                           "after_hours_order_qty": 20, "after_hours_filled_qty": 20,
                           "next_day_filled_qty": 0, "reference_price": 100,
                           "after_hours_price": 100, "next_day_open_price": ""}]).to_csv(orders / "week.csv", index=False)
            portfolio = root / "portfolio.csv"
            pd.DataFrame([{"week": "2026-W41", "execution_date": "2026-10-08",
                           "whole_nav": 100000, "A_closing_value": 50000, "A_target_exposure": .5}]).to_csv(portfolio, index=False)
            with patch.object(recalc, "ORDERS_DIR", str(orders)), patch.object(recalc, "LIVE_CSV", str(root / "live.csv")), patch.object(recalc, "PORT_CSV", str(portfolio)):
                recalc.main()
            result = pd.read_csv(portfolio)
            self.assertEqual(result.loc[0, "A_actual_exposure"], .5)
            self.assertTrue(pd.isna(result.loc[0, "A_weekly_return"]))


if __name__ == "__main__":
    unittest.main()
