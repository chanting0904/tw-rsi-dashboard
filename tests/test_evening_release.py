import datetime as dt
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import check_and_run
import publish_gate
import recovery_check
from tg_push_v5 import delivery_due
from weekly_status import write_status


class EveningReleaseTests(unittest.TestCase):
    def mode(self, today, *, holidays=None, complete=False, public_ok=True, **options):
        holidays = holidays or set()
        execution = publish_gate.last_trading_day_of_week(today, holidays)
        signal = {'rebalance_date': execution.isoformat() if execution else None, 'actions': []}
        key = publish_gate.fingerprint(signal)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'output').mkdir()
            (root / 'output/latest_signals.json').write_text(json.dumps(signal))
            (root / 'output/weekly_status.json').write_text(json.dumps({
                'state': 'ready', 'rebalance_date': signal['rebalance_date'], 'canonical_hash': key}))
            (root / 'state.json').write_text(json.dumps({'last_pushed_key': key}))
            with patch.object(recovery_check, 'ROOT', root), \
                 patch.object(recovery_check, 'load_holidays', return_value=holidays), \
                 patch.object(recovery_check, 'evaluate', return_value={'ok': complete}), \
                 patch.object(recovery_check, 'verify_public_index', side_effect=None if public_ok else RuntimeError('offline')):
                return recovery_check.recovery_mode(today, **options)[0]

    def test_thursday_evening_prepares_friday(self):
        self.assertEqual(self.mode(dt.date(2026, 10, 29), evening=True), 'full')

    def test_thursday_morning_only_updates_daily_prices(self):
        self.assertEqual(self.mode(dt.date(2026, 10, 29), daily=True), 'daily')

    def test_friday_reuses_completed_thursday_plan(self):
        self.assertEqual(self.mode(dt.date(2026, 10, 30), daily=True, complete=True), 'skip')

    def test_friday_only_repairs_publication_when_signal_exists(self):
        self.assertEqual(self.mode(dt.date(2026, 10, 30), daily=True, complete=True, public_ok=False), 'publish')

    def test_missing_evening_plan_is_computed_in_morning(self):
        self.assertEqual(self.mode(dt.date(2026, 10, 30), daily=True), 'full')

    def test_holiday_release_is_wednesday_for_thursday_execution(self):
        holidays = {'2026-10-09'}
        self.assertEqual(self.mode(dt.date(2026, 10, 7), holidays=holidays, evening=True), 'full')
        self.assertEqual(self.mode(dt.date(2026, 10, 8), holidays=holidays, evening=True), 'skip')

    def test_evening_run_delayed_past_midnight_still_publishes(self):
        # 週四 21:00 的排程被 GitHub 延到週五 04:22 才開始，仍要補發週五計畫。
        friday = dt.date(2026, 10, 30)
        self.assertEqual(self.mode(friday, evening=True, daily=True, now=dt.datetime(2026, 10, 30, 4, 22)), 'full')
        self.assertEqual(self.mode(friday, evening=True, now=dt.datetime(2026, 10, 30, 4, 50)), 'full')
        self.assertEqual(self.mode(friday, evening=True, complete=True, now=dt.datetime(2026, 10, 30, 4, 50)), 'skip')

    def test_delayed_wednesday_evening_run_does_not_publish_thursday_early(self):
        # 週三 21:45 的排程被延到週四 04:33，不可在週四收盤前提早發布。
        thursday = dt.date(2026, 10, 29)
        self.assertEqual(self.mode(thursday, evening=True, now=dt.datetime(2026, 10, 29, 4, 33)), 'skip')
        self.assertEqual(self.mode(thursday, evening=True, daily=True, now=dt.datetime(2026, 10, 29, 4, 33)), 'daily')

    def test_fully_closed_week_does_not_publish(self):
        holidays = {f'2026-10-{day}' for day in range(26, 31)}
        self.assertEqual(self.mode(dt.date(2026, 10, 29), holidays=holidays, evening=True), 'skip')

    def test_evening_status_and_notification_use_friday_date(self):
        thursday, friday = dt.date(2026, 10, 29), dt.date(2026, 10, 30)
        with tempfile.TemporaryDirectory() as folder:
            status = write_status('running', today=thursday, holidays=set(), path=Path(folder) / 'status.json')
            self.assertEqual(status['rebalance_date'], friday.isoformat())
        self.assertTrue(delivery_due(thursday, friday, signal_day=thursday, event_name='schedule'))
        self.assertFalse(delivery_due(dt.date(2026, 10, 28), friday, signal_day=thursday, event_name='schedule'))

    def test_engine_receives_execution_day_instead_of_evening_day(self):
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 29, 21, 45, tzinfo=tz)

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(check_and_run, 'ROOT', folder), \
             patch.object(check_and_run, 'HOLS', set()), \
             patch.object(check_and_run, 'FORCE', False), \
             patch.object(check_and_run.datetime, 'datetime', Clock), \
             patch.object(check_and_run.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
             patch('publish_gate.evaluate', return_value={'ok': True, 'rebalance_date': '2026-10-30', 'canonical_hash': 'fixture'}), \
             patch('weekly_status.write_status'):
            with self.assertRaises(SystemExit) as end:
                check_and_run.main()
            self.assertEqual(end.exception.code, 0)
            self.assertEqual(run.call_args_list[0].args[0][-2:], ['--live-rebalance', '2026-10-30'])

    def test_staged_engine_receives_published_holdings_checkpoints(self):
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 5, 21, 0, tzinfo=tz)

        seen = []

        def engine(command, env=None, **_):
            if env and 'V5_OUTPUT_DIR' in env:
                seen.append((Path(env['V5_OUTPUT_DIR']) / 'live_checkpoints/2026-10-30.json').exists())
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'output/live_checkpoints').mkdir(parents=True)
            (Path(folder) / 'output/live_checkpoints/2026-10-30.json').write_text('{}')
            with patch.object(check_and_run, 'ROOT', folder), \
                 patch.object(check_and_run, 'HOLS', set()), \
                 patch.object(check_and_run, 'FORCE', False), \
                 patch.object(check_and_run.datetime, 'datetime', Clock), \
                 patch.object(check_and_run.subprocess, 'run', side_effect=engine), \
                 patch('publish_gate.evaluate', return_value={'ok': True, 'rebalance_date': '2026-11-06', 'canonical_hash': 'fixture'}), \
                 patch('weekly_status.write_status'):
                with self.assertRaises(SystemExit):
                    check_and_run.main()
        self.assertEqual(seen, [True])


if __name__ == '__main__':
    unittest.main()
