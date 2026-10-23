"""Project disclosed corporate actions into a prior-close rebalance plan.

Selection still sees only the prior actual close. Reference prices mechanically
remove known cash/stock rights; they are not today's observed trading price.
"""
import math
import pandas as pd


def prepare_live_actions(ctx):
    ctx.live_action_events={}
    ctx.live_action_unverified=[]
    ctx.live_action_prices={}
    if ctx.live_day is None:
        return
    day,basis=pd.Timestamp(ctx.live_day),pd.Timestamp(ctx.signal_day)
    for code,event in (ctx.corporate_events or {}).get(day,{}).items():
        cash=float(event.get('cash',0.))
        ratio=float(event.get('ratio',1.))
        required=[]
        if cash>0:required.append('cash_known_on')
        if abs(ratio-1)>1e-8:required.append('ratio_known_on')
        verified=all(event.get(key) is not None and pd.notna(event[key]) and pd.Timestamp(event[key])<=basis for key in required)
        if 'unresolved_factor' in event or not verified:
            ctx.live_action_unverified.append(code)
            for name in ['close','open_','high','low']:
                frame=getattr(ctx,name)
                if code in frame.columns:frame.loc[day,code]=float('nan')
            continue
        if not required:continue
        previous=ctx.close.at[basis,code] if code in ctx.close.columns else float('nan')
        if pd.isna(previous) or previous<=0:
            ctx.live_action_unverified.append(code)
            continue
        reference=(float(previous)-cash)/ratio if ratio>0 else float('nan')
        if not math.isfinite(reference) or reference<=0:
            raise ValueError(f'無法核對除權息參考價：{code} {day.date()}')
        # Always derive from the actual basis row, making repeated runs stable.
        for name in ['close','open_','high','low']:
            frame=getattr(ctx,name)
            if code in frame.columns:frame.loc[day,code]=reference
        ctx.live_action_events[code]=dict(event)
        ctx.live_action_prices[code]={'basis_date':str(basis.date()),'effective_date':str(day.date()),
            'previous_close':float(previous),'cash_per_share':cash,'share_ratio':ratio,'reference_price':reference}
