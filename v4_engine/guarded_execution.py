"""Execute the browser's guarded planner against a separate real cash/share ledger.

The legacy V4 path supplies dated selection/reference weights only. It never
supplies the executed account's quantities or NAV. One JS policy computes the
actual final quantities in backtests and the browser, preventing policy drift.
"""
import copy
import json
import math
import subprocess
from pathlib import Path
import pandas as pd
from .canonical import canonical_for
from .jsonio import clean_json
from .fees import OrderFees
from .security_status import can_trade
from .valuation import restricted_assets


class GuardedExecution:
    def __init__(self, ctx, reference):
        self.reference = {p['date']:p for p in reference['weekly_plans']}
        self.trades = reference['trades_df']
        self.previous_date = None
        self.previous_capital = ctx.config.start_capital
        self.ctx = ctx
        self.process = None

    def __enter__(self):
        self.process = subprocess.Popen(['node',str(Path(__file__).with_name('guarded_bridge.cjs'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        return self

    def __exit__(self, *args):
        if self.process:
            self.process.stdin.close()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
            self.process.stdout.close();self.process.stderr.close()

    def __call__(self, ctx, positions, cash, day, mode, coefs, limit_up, limit_dn, record=True):
        ref = self.reference.get(day)
        if ref is None:raise ValueError('Missing dated V4 reference allocation')
        signal = canonical_for(ctx,ref,self.trades,'reference-weights-only')
        actions = {a['stock_code']:a for a in signal['actions']}
        px = ctx.open_.loc[day] if mode=='open' else ctx.close.loc[day]
        marked = ctx.valuation_close.loc[day]
        pre = {c:p['shares']-p.get('locked_shares',0) for c,p in positions.items()}
        stocks = sum(q*float(marked[c]) for c,q in pre.items() if pd.notna(marked[c]) and marked[c]>0)
        reserve = float(getattr(ctx,'receivable_total',0.))
        capital = cash+stocks+reserve
        # The actual corporate ledger has already applied share deliveries/splits.
        # Neutralize only the browser's model-ratio adapter; use actual quantities.
        for code,qty in pre.items():
            if qty and code not in actions:
                actions[code] = dict(stock_code=code,stock_name=(ctx.name_map or {}).get(code,code),
                    channel=positions[code]['pool'],rank=None,action='SELL',allocation_eligible=False,
                    target_weight=0.,strategy_target_weight=0.,post_shares=0,current_shares=1,price=None)
        for a in actions.values():a['current_shares']=1
        quotes={}
        for code in set(pre)|set(actions):
            raw=float(px.get(code,float('nan')))
            mark=float(marked.get(code,float('nan')))
            valid=math.isfinite(raw) and raw>0
            price=raw if valid else (mark if math.isfinite(mark) and mark>0 else None)
            allowed=valid and can_trade(ctx,code,day)
            quotes[code]=dict(price=price,tradable=bool(allowed),
                buyable=bool(allowed and not (code in limit_up and limit_up.at[day,code])),
                sellable=bool(allowed and not (code in limit_dn and limit_dn.at[day,code])))
        restricted=restricted_assets(ctx,positions,day)
        signal.update(actions=list(actions.values()),restricted_assets=restricted)
        previous=dict(rebalance_date=str((day-pd.Timedelta(days=7)).date()) if self.previous_date is None else self.previous_date,
            actions=[dict(stock_code=c,post_shares=1) for c,q in pre.items() if q])
        args=dict(previous=previous,current=signal,previousCapital=self.previous_capital,capital=capital,quotes=quotes,
            reservedAssets=reserve,snapshot=dict(schema=1,rebalance_date=previous['rebalance_date'],capital=self.previous_capital,
            holdings=[dict(code=c,shares=int(q)) for c,q in pre.items() if q]))
        # The public input floor is a UI constraint, not a reason to stop a small
        # test account or an account that suffered losses. Planner supports that
        # explicitly only for trusted server simulations.
        args['simulation']=True
        args['adjustmentsApplied']=True
        self.process.stdin.write(json.dumps(clean_json(args),ensure_ascii=False,allow_nan=False)+'\n');self.process.stdin.flush()
        line=self.process.stdout.readline()
        if not line:raise ValueError('Shared allocation runtime stopped unexpectedly')
        reply=json.loads(line)
        if not reply['ok']:raise ValueError('Shared allocation policy: '+reply['error'])
        plan=reply['plan'];fills=[];fees=OrderFees(ctx.config,getattr(ctx,'security_types',None))
        rows={r['code']:r for r in plan['rows']}
        # Net orders charge one minimum per stock/side, just as the browser does.
        for r in plan['rows']:
            change=int(r['change']);code=r['code'];price=float(r['price'])
            if not change:continue
            side='BUY' if change>0 else 'SELL';quantity=abs(change)
            cost=fees.charge(code,side,quantity*price)
            if abs(cost['fee']-r['fee'])>1e-6:raise ValueError('Web/backtest transaction fees disagree')
            cash += (-quantity*price if change>0 else quantity*price)-cost['fee']
            old=positions.get(code);locked=old.get('locked_shares',0) if old else 0
            is_new=pre.get(code,0)==0
            if change>0:
                if old:
                    old['avg']=(old['avg']*old['shares']+quantity*price+cost['fee'])/(old['shares']+quantity)
                    old['shares']+=quantity
                else:positions[code]=dict(shares=quantity,avg=price+cost['fee']/quantity,pool=r['channel'],entry_d=day,wc=1.)
            else:positions[code]['shares']-=quantity
            empty=positions[code]['shares']==0
            full=empty or (side=='SELL' and r['target']==0)
            if empty:del positions[code]
            fills.append(dict(date=day,code=code,pool=r['channel'],side=side,price=price,shares=quantity,
                amount=quantity*price,rank=r.get('rank'),reason=r['reason'],full=full,is_new=is_new,**cost))
        if cash < -1e-6:raise ValueError('Actual cash ledger cannot fund shared plan')
        after=sum((p['shares']-p.get('locked_shares',0))*float(marked[c]) for c,p in positions.items() if pd.notna(marked[c]) and marked[c]>0)
        nav=cash+after+reserve
        if abs(nav-(capital-plan['fees']))>max(1e-5,abs(capital)*1e-10):raise ValueError('Shared plan ledger does not reconcile')
        recs=[]
        for code,a in actions.items():
            held=positions.get(code,{});qty=held.get('shares',0)-held.get('locked_shares',0)
            r=rows.get(code);price=r['price'] if r else float(marked.get(code,0.))
            if not qty and not pre.get(code,0) and not a.get('allocation_eligible'):continue
            if not price or not math.isfinite(price) or price<=0:continue
            weight=a.get('strategy_target_weight',0.) if a.get('allocation_eligible') else 0.
            recs.append(dict(date=day,pool=a['channel'],code=code,rank=a.get('rank'),cur_sh=qty,
                target_sh=int(capital*weight/price),cur_val=qty*price,target_val=capital*weight,
                pre_sh=pre.get(code,0),pre_val=pre.get(code,0)*price,pre_total=capital,nav=nav,price=price,
                planner_target_weight=a.get('target_weight',0.) if a.get('allocation_eligible') else 0.,
                strategy_target_weight=weight))
        blocked=sorted(c for c,q in pre.items() if q and c in quotes and not quotes[c]['sellable'])
        exposure=after/nav if nav>0 else 0.
        cap_blocked=blocked if exposure>ctx.config.exposure_max+1e-10 else []
        if exposure>ctx.config.exposure_max+1e-10 and any(q and quotes.get(c,{}).get('sellable') for c,q in ((c,p['shares']-p.get('locked_shares',0)) for c,p in positions.items())):
            raise ValueError('Unexplained exposure cap violation')
        self.previous_date=str(day.date());self.previous_capital=capital
        wp=dict(date=day,cash=cash,nav=nav,capital_basis=capital,receivables=reserve,recs=recs,stock_exposure=exposure,
            exposure_in_band=.90<=exposure<=.95,missing_price_targets=ref.get('missing_price_targets',0),
            blocked_limitup_targets=sum(1 for c,a in actions.items() if a.get('allocation_eligible') and not pre.get(c,0) and not quotes.get(c,{}).get('buyable') and quotes.get(c,{}).get('tradable')),
            reference_blocked_limitup_targets=ref.get('blocked_limitup_targets',0),
            blocked_exit_codes=blocked,exposure_cap_blocked_codes=cap_blocked,unplanned_holdings=[],
            restricted_assets=restricted_assets(ctx,positions,day),pending_assets=getattr(ctx,'share_entitlements',[]) or [],
            wa_eff=ref['wa_eff'],wb_eff=ref['wb_eff'],execution_policy='guarded_weekly_capital_v1')
        return max(0.,cash),fills,wp if record else None
