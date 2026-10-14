"""Retry one pre-delivery command. Never wraps Telegram delivery."""
import argparse
import subprocess
import time
from weekly_status import write_status


def run_with_retries(command, *, attempts=3, delays=(60, 180), weekly=False,
                     run=subprocess.run, sleep=time.sleep, report=write_status):
    if attempts < 1:
        raise ValueError('attempts must be positive')
    for attempt in range(1, attempts + 1):
        if weekly:
            report('running', attempt=attempt, max_attempts=attempts)
        try:
            result = run(command, check=False)
            code = result.returncode
        except OSError:
            code = 1
        if code == 0:
            return 0
        final = attempt == attempts
        if weekly:
            report('failed' if final else 'retrying', attempt=attempt, max_attempts=attempts)
        print(f'[retry] attempt {attempt}/{attempts}, exit={code}', flush=True)
        if final:
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
