"""Retry one pipeline command; keep the last error line for the failure alert."""
import argparse
from pathlib import Path
import subprocess
import sys
import time
from weekly_status import write_status

REASON = Path(__file__).resolve().parent / 'output' / 'last_error.txt'


def save_reason(error, path=REASON):
    """最後一次仍失敗時，留下錯誤的最後一行，讓 TG 告警直接說明原因。"""
    lines = [line.strip() for line in (error or '').splitlines() if line.strip() and not line.startswith('[retry]')]
    if lines:
        path.parent.mkdir(exist_ok=True)
        path.write_text(lines[-1][:800], encoding='utf-8')


def run_with_retries(command, *, attempts=3, delays=(60, 180), weekly=False,
                     run=subprocess.run, sleep=time.sleep, report=write_status):
    if attempts < 1:
        raise ValueError('attempts must be positive')
    for attempt in range(1, attempts + 1):
        if weekly:
            report('running', attempt=attempt, max_attempts=attempts)
        error = None
        try:
            result = run(command, check=False, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
            code = result.returncode
            error = result.stderr if isinstance(getattr(result, 'stderr', None), str) else None
            if error:
                sys.stderr.write(error)
                sys.stderr.flush()
        except OSError:
            code = 1
        if code == 0:
            return 0
        final = attempt == attempts
        if weekly:
            report('failed' if final else 'retrying', attempt=attempt, max_attempts=attempts)
        print(f'[retry] attempt {attempt}/{attempts}, exit={code}', flush=True)
        if final:
            save_reason(error)
            return code
        delay = delays[min(attempt - 1, len(delays) - 1)]
        print(f'[retry] retry in {delay}s', flush=True)
        sleep(delay)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weekly', action='store_true')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('command required')
    raise SystemExit(run_with_retries(command, weekly=args.weekly))


if __name__ == '__main__':
    main()
