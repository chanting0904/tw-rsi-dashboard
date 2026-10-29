import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from publication_check import embedded_data
from v5_engine.jsonio import write_json
from v5_engine.pipeline import preserve_previous_capital_signal

ROOT = Path(__file__).resolve().parents[1]


class CapitalSignalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.previous = {'rebalance_date': '2026-10-23', 'price_basis_date': '2026-10-22',
                         'actions': [{'stock_code': '2466', 'price': 261., 'post_weight': .06144}]}
        self.current = dict(self.previous, rebalance_date='2026-10-30', price_basis_date='2026-10-29')

    def read_archive(self):
        return json.loads((self.output / 'previous_capital_signal.json').read_text(encoding='utf-8'))

    def test_advancing_week_keeps_exact_previous_published_plan(self):
        write_json(self.output / 'latest_signals.json', self.previous)
        write_json(self.output / 'history/2026-10-23.json', dict(self.previous, actions=[{'price': 300.}]))
        preserve_previous_capital_signal(self.output, self.current)
        self.assertEqual(self.read_archive(), self.previous)

    def test_same_week_retry_preserves_the_predecessor(self):
        write_json(self.output / 'latest_signals.json', self.previous)
        preserve_previous_capital_signal(self.output, self.current)
        write_json(self.output / 'latest_signals.json', self.current)
        preserve_previous_capital_signal(self.output, dict(self.current, generated_at='later'))
        self.assertEqual(self.read_archive(), self.previous)

    def test_interrupted_advance_is_idempotent(self):
        write_json(self.output / 'latest_signals.json', self.previous)
        preserve_previous_capital_signal(self.output, self.current)
        preserve_previous_capital_signal(self.output, self.current)
        self.assertEqual(self.read_archive(), self.previous)

    def test_first_publish_does_not_invent_a_previous_plan(self):
        preserve_previous_capital_signal(self.output, self.current)
        self.assertFalse((self.output / 'previous_capital_signal.json').exists())

    def test_backward_or_malformed_publication_preserves_existing_files(self):
        write_json(self.output / 'latest_signals.json', self.current)
        for invalid in [self.previous, {}, {'rebalance_date': 'broken', 'actions': []}]:
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, KeyError)):
                preserve_previous_capital_signal(self.output, invalid)
        self.assertFalse((self.output / 'previous_capital_signal.json').exists())

    def test_corrupted_or_forward_archive_stops_without_overwriting(self):
        write_json(self.output / 'latest_signals.json', self.previous)
        archive = self.output / 'previous_capital_signal.json'
        for value in ['broken JSON', json.dumps(self.current), json.dumps({'actions': []})]:
            archive.write_text(value, encoding='utf-8')
            with self.subTest(value=value), self.assertRaises((ValueError, KeyError)):
                preserve_previous_capital_signal(self.output, self.current)
            self.assertEqual(archive.read_text(encoding='utf-8'), value)

    def test_web_uses_published_prices_and_keeps_separate_corporate_reference(self):
        temp = self.output
        assets = ['update_data.py', 'dashboard_ui.css', 'dashboard_ui.js', 'monthly_details.js',
                  'performance_ui.js', 'capital_preview.js', 'weekly_status.js', 'order_planner.js',
                  'order_planner_ui.js', 'weekly_capital.js', 'weekly_capital_ui.js', 'holidays.json']
        for name in assets:
            (temp / name).write_bytes((ROOT / name).read_bytes())
        for name in ['latest_signals.json', 'perf.json', 'validation_log.json']:
            target = temp / 'output' / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes((ROOT / 'output' / name).read_bytes())
        current = json.loads((ROOT / 'output/latest_signals.json').read_text(encoding='utf-8'))
        paths = sorted(p for p in (ROOT / 'output/history').glob('*.json') if p.stem < current['rebalance_date'])
        model = json.loads(paths[-1].read_text(encoding='utf-8'))
        published = copy.deepcopy(model)
        published['actions'][0]['price'] = float(model['actions'][0]['price']) + 17
        write_json(temp / 'output/history' / paths[-1].name, model)
        write_json(temp / 'output/previous_capital_signal.json', published)
        process = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(temp / 'update_data.py')],
                                 env=dict(os.environ, V5_SKIP_LOCAL_UPDATE='1', PYTHONPATH=str(ROOT)),
                                 capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        data = embedded_data((temp / 'index.html').read_text(encoding='utf-8'))
        self.assertEqual(data['previous_capital_signal'], published)
        self.assertEqual(data['previous_model_signal'], model)


if __name__ == '__main__':
    unittest.main()
