"""Public workflow state; never includes exception text, credentials or holdings."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from publish_gate import load_holidays, last_trading_day_of_week, prev_trading_day_str, fingerprint

ROOT = Path(__file__).resolve().parent
STATUS_FILE = ROOT / 'output' / 'weekly_status.json'


def write_status(state, *, stage='signal', attempt=1, max_attempts=3,
                 today=None, path=None, holidays=None):
    now = dt.datetime.now(ZoneInfo('Asia/Taipei'))
    today = today or now.date()
    holidays = load_holidays() if holidays is None else holidays
    expected = last_trading_day_of_week(today, holidays)
    release_day = (dt.date.fromisoformat(prev_trading_day_str(expected, holidays))
                   if expected else None)
    # Both runs describe the execution date, never the evening publication date.
    if today not in (release_day, expected) or expected is None:
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


def sync_ready_status(signal, validation, performance, *, path=None):
    """Keep an already-ready dated plan bound to its verified release."""
    from version import RELEASE_VERSION
    from v5_engine.jsonio import validation_matches
    destination = Path(path) if path else STATUS_FILE
    try:
        status = json.loads(destination.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    verified = (status.get('state') == 'ready'
                and status.get('rebalance_date') == signal.get('rebalance_date')
                and signal.get('release_version') == RELEASE_VERSION
                and performance.get('release_version') == RELEASE_VERSION
                and validation.get('verdict') == 'PASS'
                and validation_matches(signal, validation, performance))
    if verified and status.get('canonical_hash') != fingerprint(signal):
        status = {**status, 'canonical_hash': fingerprint(signal),
                  'release_version': RELEASE_VERSION,
                  'updated_at': dt.datetime.now(ZoneInfo('Asia/Taipei')).isoformat()}
        temporary = destination.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(destination)
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('state', choices=['running', 'retrying', 'ready', 'failed'])
    parser.add_argument('--stage', default='pipeline')
    args = parser.parse_args()
    write_status(args.state, stage=args.stage)


if __name__ == '__main__':
    main()
