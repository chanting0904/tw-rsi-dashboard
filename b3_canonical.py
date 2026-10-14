"""B3 command-line entry point. Importing this file performs no network or writes."""
import argparse
import os
from pathlib import Path
from b3_engine.config import StrategyConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description='B3 verified weekly simulation')
    parser.add_argument('--live-rebalance', help='Weekly execution date; use previous-session close')
    parser.add_argument('--capital', type=float, default=100_000, help='Initial simulation capital; nondefault values require --report-only')
    parser.add_argument('--report-only', action='store_true', help='Write completed performance only; never replace signals')
    parser.add_argument('--end', help='Historical cutoff date for reports')
    args = parser.parse_args(argv)
    if args.capital != 100_000 and not args.report_only:
        parser.error('非預設本金請使用 --report-only，避免替換共用的週調倉訊號')
    if args.live_rebalance and (args.report_only or args.end):
        parser.error('本週計畫不能同時使用報表模式或歷史截止日')
    try:
        config = StrategyConfig(start_capital=args.capital)
        from b3_engine.market import load_market
        from b3_engine.pipeline import publish
        root = Path(__file__).resolve().parent
        context = load_market(config, root, live_rebalance=args.live_rebalance,
                              data_root=os.environ.get('B3_DATA_ROOT'))
        publish(context, os.environ.get('B3_OUTPUT_DIR', root / 'output'), root,
                end=args.end, report_only=args.report_only)
        print('B3 完成：', '績效報表' if args.report_only else '已驗證訊號')
        return 0
    except Exception as error:
        print(f'B3 停止：{type(error).__name__}: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
