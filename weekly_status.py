"""Public workflow state; never includes exception text, credentials or holdings."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from publish_gate import load_holidays, last_trading_day_of_week, fingerprint

ROOT = Path(__file__).resolve().parent
STATUS_FILE = ROOT / 'output' / 'weekly_status.json'


def write_status(state, *, stage='signal', attempt=1, max_attempts=3,
                 today=None, path=None, holidays=None):
    now = dt.datetime.now(ZoneInfo('Asia/Taipei'))
    today = today or now.date()
    holidays = load_holidays() if holidays is None else holidays
    expected = last_trading_day_of_week(today, holidays)
    # A daily refresh or a midweek deployment must not change weekly readiness.
    if expected != today:
        return None
    result = {'state': state, 'rebalance_date': expected.isoformat(),
              'updated_at': now.isoformat(), 'stage': stage,
              'attempt': attempt, 'max_attempts': max_attempts,
              'run_id': os.environ.get('GITHUB_RUN_ID', '')}
    if state == 'ready':
        signal = json.loads((ROOT / 'output/latest_signals.json').read_text(encoding='utf-8'))
        if signal.get('rebalance_date') != expected.isoformat():
            raise ValueError('不能把舊訊號標記為本週已完成')
        result['canonical_hash'] = fingerprint(signal)
    destination = Path(path) if path else STATUS_FILE
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(destination)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('state', choices=['running', 'retrying', 'ready', 'failed'])
    parser.add_argument('--stage', default='pipeline')
    args = parser.parse_args()
    write_status(args.state, stage=args.stage)


if __name__ == '__main__':
    main()
