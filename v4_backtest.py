"""Research report entry point using the same V4 model as weekly publication."""
import argparse
import csv
import os
from pathlib import Path
from v4_engine.config import StrategyConfig


def write_report(ctx, result, destination, root):
    from v4_engine.canonical import classify_day, canonical_for
    from v4_engine.jsonio import write_json
    from v4_engine.performance import perf_document
    from v4_engine.pipeline import commit_sha
    from tg_push_v4 import build_message
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / 'perf.json', perf_document(result, ctx.config))
    cutoff = ctx.close.index[-20] if len(ctx.close) >= 20 else ctx.close.index[0]
    recent = [plan for plan in result['weekly_plans'] if plan['date'] >= cutoff]
    names = ctx.name_map or {}
    fields = ['date', 'action', 'stock_id', 'stock_name', 'channel', 'rank', 'reason',
              'current_weight', 'target_weight', 'weight_diff', 'current_shares',
              'target_shares', 'trade_shares', 'estimated_price', 'estimated_amount', 'cash_after_trade']
    with (destination / 'daily_trade_plan.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for plan in recent:
            recommendations = {row['stock_code']: row for row in canonical_for(ctx, plan, result['trades_df'], commit_sha(root))['actions']}
            for action in classify_day(plan, result['trades_df']):
                recommended = recommendations[action['code']]
                writer.writerow({'date': plan['date'].strftime('%Y-%m-%d'), 'action': action['action'],
                    'stock_id': action['code'], 'stock_name': names.get(action['code'], action['code']),
                    'channel': action['pool'], 'rank': action.get('rank'), 'reason': action.get('reason', ''),
                    'current_weight': action['current_weight'], 'target_weight': recommended['target_weight'],
                    'weight_diff': recommended['weight_diff'], 'current_shares': action['pre_sh'],
                    'target_shares': recommended['target_shares'], 'trade_shares': action['trade_sh'],
                    'estimated_price': action['price'], 'estimated_amount': action['amount'], 'cash_after_trade': plan['cash']})
    lines = [f"V4 completed simulation through {result['nav'].index[-1].date()}",
             f"CAGR {result['cagr']:.2%} | MDD {result['mdd']:.2%} | PF {result['pf']:.2f} | Win rate {result['winrate']:.1f}%",
             'All fills and fees included in completed cycles; yearly returns use the prior-year closing NAV.',
             'Below are simulated historical message examples; nothing is sent.']
    for plan in recent[-5:]:
        lines.append(build_message(canonical_for(ctx, plan, result['trades_df'], commit_sha(root))))
    (destination / '_final_candidate_report.txt').write_text('\n\n'.join(lines), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='V4 research reports; no public signal writes or Telegram delivery')
    parser.add_argument('--capital', type=float, default=100_000)
    parser.add_argument('--output-dir')
    args = parser.parse_args(argv)
    from v4_engine.market import load_market
    from v4_engine.simulation import run
    root = Path(__file__).resolve().parent
    ctx = load_market(StrategyConfig(start_capital=args.capital), root, data_root=os.environ.get('V4_DATA_ROOT'))
    result = run(ctx)
    destination = Path(args.output_dir) if args.output_dir else root / 'research_output'
    write_report(ctx, result, destination, root)
    print('Research reports:', destination)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
