"""Verify a database migration against the last published daily NAV and plan."""
import argparse
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from v5_engine.jsonio import document_digest, validation_matches


def verify(root, baseline, previous):
    root = Path(root)
    for name in ('history_prices.sqlite3', 'history_reference.sqlite3', 'official.sqlite3'):
        with closing(sqlite3.connect(f'file:{(root / "local_db" / name).as_posix()}?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('資料庫完整性錯誤：' + name)
    expected = json.loads(Path(baseline).read_text(encoding='utf-8'))
    current = json.loads((root / 'output/perf.json').read_text(encoding='utf-8'))
    old_nav = dict(expected['nav'])
    new_nav = dict(current['nav'])
    if any(day not in new_nav or abs(float(value) - float(new_nav[day])) > .011 for day, value in old_nav.items()):
        raise ValueError('歷史每日資產與已發布版本不同')
    old_signal = json.loads(Path(previous).read_text(encoding='utf-8'))
    carry = json.loads((root / 'output/previous_capital_signal.json').read_text(encoding='utf-8'))
    if document_digest(carry) != document_digest(old_signal):
        raise ValueError('上一份已發布股數基準未完整保留')
    signal = json.loads((root / 'output/latest_signals.json').read_text(encoding='utf-8'))
    validation = json.loads((root / 'output/validation_log.json').read_text(encoding='utf-8'))
    if not validation_matches(signal, validation, current) or validation.get('verdict') != 'PASS':
        raise ValueError('本週訊號尚未完整驗證')
    status = json.loads((root / 'output/data_status.json').read_text(encoding='utf-8'))
    if status['status'] != 'PASS' or status['latest_price_date'] != signal['price_basis_date']:
        raise ValueError('本地資料與本週訊號日期不符')
    result = dict(status='PASS', historical_nav_points=len(old_nav),
        rebalance_date=signal['rebalance_date'], price_basis_date=signal['price_basis_date'],
        stock_exposure=signal['stock_exposure'], previous_plan_preserved=True)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--previous', required=True)
    args = parser.parse_args()
    verify(Path(__file__).parent, args.baseline, args.previous)
