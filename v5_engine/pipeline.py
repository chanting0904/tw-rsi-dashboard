from version import STRATEGY_VERSION, RELEASE_VERSION
"""Orchestrate historical performance, prospective plans and verified publication."""
import datetime
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo
import pandas as pd
from .canonical import canonical_for
from .config import V5_COEFFICIENTS
from .jsonio import write_json, document_digest
from .performance import perf_document
from .simulation import run, run_seeded
from .validation import validate_plans


def commit_sha(root):
    if os.environ.get('GITHUB_SHA'):
        return os.environ['GITHUB_SHA'][:8]
    try:
        return subprocess.check_output(['git', 'rev-parse', '--short=8', 'HEAD'], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return 'dev'


def fixed_anchor(result, config):
    import json
    expected = json.loads((Path(__file__).resolve().parents[1] / 'v5_baseline.json').read_text(encoding='utf-8'))
    anchor_day = pd.Timestamp(expected['date'])
    nav = result['nav'].loc[:anchor_day, 'nav']
    if nav.empty:
        return None, False
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (float(nav.iloc[-1]) / config.start_capital) ** (1 / years) - 1 if years > 0 else 0
    mdd = float((nav / nav.cummax().clip(lower=config.start_capital) - 1).min())
    parameters = {'threshold': config.threshold, 'buy_fee': config.buy_fee, 'sell_fee': config.sell_fee,
                  'minimum_commission': config.minimum_commission,
                  'delisted_valuation': config.delisted_valuation, 'nav_frequency': 'daily'}
    selection_rules = {'selection_mode':'single_ranked_pool','a_rank_days': config.a_rank_days, 'a_entry_rank': config.max_hold_a,
                       'a_retention_rank': config.a_retention_rank}
    ok = (result['nav'].index[-1] >= anchor_day and parameters == expected['parameters']
          and selection_rules == expected.get('selection_rules')
          and result.get('execution_policy') == expected.get('execution_policy')
          and abs(cagr - expected['cagr']) < .005 and abs(mdd - expected['mdd']) < .005)
    return {'date': expected['date'], 'cagr': cagr, 'mdd': mdd, 'release_version': RELEASE_VERSION,
            'selection_rules': selection_rules}, bool(ok)


def publish(ctx, output_dir, root, *, end=None, report_only=False):
    output_dir = Path(output_dir)
    historical_end = ctx.live_day - pd.Timedelta(days=ctx.live_day.weekday() + 3) if ctx.live_day is not None else end
    result = run(ctx, coefs=V5_COEFFICIENTS, end=historical_end)
    anchor, anchor_ok = fixed_anchor(result, ctx.config)
    if report_only:
        if ctx.config.start_capital == 100_000 and not anchor_ok:
            raise ValueError('固定期間回歸基準未通過，不更新正式績效')
        document = perf_document(result, ctx.config, anchor, ctx=ctx)
        write_json(output_dir / 'perf.json', document)
        return {'result': result, 'perf': document, 'report_only': True}
    from .live_model import load_seed
    seed=load_seed(root)
    if seed is None:
        raise ValueError('缺少 V5 持倉銜接快照，停止發布，避免重建成不同持股')
    seeded=seed is not None and ctx.close.index[-1]>pd.Timestamp(seed['date'])
    live_result = run_seeded(ctx,seed,coefs=V5_COEFFICIENTS,end=end) if seeded else (run(ctx, coefs=V5_COEFFICIENTS) if ctx.live_day is not None else result)
    plans, trades = live_result['weekly_plans'], live_result['trades_df']
    if not plans:
        raise ValueError('沒有可以發布的調倉計畫')
    latest = plans[-1]
    status = getattr(ctx,'security_status',None)
    if not status or status.get('checked_for') != latest['date'].strftime('%Y-%m-%d'):
        raise ValueError('缺少本期官方交易狀態核對，停止發布')
    historical = [plan for plan in plans if ctx.live_day is None or plan['date'] != ctx.live_day][-20:]
    checks, failures, warnings = validate_plans(ctx, historical[-19:] + [latest] if ctx.live_day is not None else historical, trades)
    valid = anchor_ok and not failures
    validation = {'strategy_version': STRATEGY_VERSION, 'release_version': RELEASE_VERSION, 'generated_at': datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
                  'rebalance_date': latest['date'].strftime('%Y-%m-%d'), 'v5_consistency': anchor_ok,
                  'fixed_anchor': {'pass': anchor_ok, 'values': anchor}, 'warning_checks': warnings,
                  'checks': {key: {**value, 'detail': value['detail'][-5:] if key == 'exposure_upper_bound' else value['detail'][:5]} for key, value in checks.items()},
                  'verdict': 'PASS' if valid else 'FAIL'}
    if not valid:
        write_json(output_dir / 'validation_log.json', validation)
        raise ValueError(f'validation FAIL: {failures}; fixed_anchor={anchor_ok}')
    write_json(output_dir / 'security_status.json', status)
    from .share_delivery import save_delivery_snapshot
    save_delivery_snapshot(ctx,live_result,output_dir)
    sha = commit_sha(root)
    if seeded:
        previous=seed['previous_signal']
        write_json(output_dir/'history'/(previous['rebalance_date']+'.json'),previous)
    for plan in historical:
        write_json(output_dir / 'history' / (plan['date'].strftime('%Y-%m-%d') + '.json'), canonical_for(ctx, plan, trades, sha))
    document = perf_document(result, ctx.config, anchor, ctx=ctx)
    signal = canonical_for(ctx, latest, trades, sha)
    if seeded:signal['live_model_origin']=live_result['live_model_origin']
    validation['signal_digest']=document_digest(signal)
    validation['performance_digest']=document_digest(document)
    write_json(output_dir / 'validation_log.json', validation)
    write_json(output_dir / 'corporate_actions.json', live_result.get('corporate_action_audit', []))
    write_json(output_dir / 'perf.json', document)
    write_json(output_dir / 'latest_signals.json', signal)
    print(f"validation PASS | date={signal['rebalance_date']} | exposure={signal['stock_exposure']:.2%}")
    return {'result': result, 'live_result': live_result, 'perf': document, 'signal': signal, 'validation': validation}
