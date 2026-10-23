"""V5 command-line entry point. Importing this file performs no network or writes."""
import argparse
import os
from pathlib import Path
from v5_engine.config import StrategyConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description='V5 verified weekly simulation')
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
        from v5_engine.market import load_market
        from v5_engine.pipeline import publish
        root = Path(__file__).resolve().parent
        default_output = root / 'audit_reports' / ('capital_' + format(args.capital, 'g')) if args.report_only else root / 'output'
        output = Path(os.environ.get('V5_OUTPUT_DIR') or default_output)
        if args.report_only and output.resolve() == (root / 'output').resolve():
            raise ValueError('研究報表不得覆蓋正式output，請另指定資料夾')
        context = load_market(config, root, live_rebalance=args.live_rebalance,
                              data_root=os.environ.get('V5_DATA_ROOT'))
        publish(context, output, root,
                end=args.end, report_only=args.report_only)
        print('V5 完成：', '績效報表' if args.report_only else '已驗證訊號')
        return 0
    except Exception as error:
        print(f'V5 停止：{type(error).__name__}: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
