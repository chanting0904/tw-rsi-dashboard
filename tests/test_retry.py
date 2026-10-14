import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from retry_runner import run_with_retries
from weekly_status import write_status


class RetryTests(unittest.TestCase):
    def test_first_success_has_no_sleep(self):
        run, sleep, report = Mock(return_value=SimpleNamespace(returncode=0)), Mock(), Mock()
        self.assertEqual(run_with_retries(['offline'], run=run, sleep=sleep, report=report), 0)
        sleep.assert_not_called()
        report.assert_not_called()

    def test_transient_failure_recovers_on_third_attempt(self):
        run = Mock(side_effect=[SimpleNamespace(returncode=n) for n in [3, 3, 0]])
        sleep, report = Mock(), Mock()
        self.assertEqual(run_with_retries(['offline'], weekly=True, run=run, sleep=sleep, report=report), 0)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [60, 180])
        self.assertNotIn('failed', [c.args[0] for c in report.call_args_list])

    def test_persistent_failure_returns_failure_and_stops(self):
        run, sleep, report = Mock(return_value=SimpleNamespace(returncode=2)), Mock(), Mock()
        self.assertEqual(run_with_retries(['offline'], weekly=True, run=run, sleep=sleep, report=report), 2)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(report.call_args.args[0], 'failed')

    def test_midweek_failure_does_not_write_weekly_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            self.assertIsNone(write_status('failed', today=dt.date(2026, 10, 14), path=path, holidays=set()))
            self.assertFalse(path.exists())

    def test_public_state_does_not_leak_error_or_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            write_status('failed', today=dt.date(2026, 10, 16), path=path, holidays=set())
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['rebalance_date'], '2026-10-16')
            self.assertEqual(data['state'], 'failed')
            self.assertNotIn('error', data)


if __name__ == '__main__':
    unittest.main()
