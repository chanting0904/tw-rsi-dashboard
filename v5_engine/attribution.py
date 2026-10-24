"""Monthly stock contributions reconciled against the historical strategy NAV."""
from collections import defaultdict
import json
import math
from pathlib import Path
import pandas as pd
from .valuation import prepare_valuation

def monthly_contributions(ctx, result):
    nav=result['nav']['nav']
    marks=prepare_valuation(ctx,ctx.close)
    records=defaultdict(list)
    for row in result['trades_df'].to_dict('records'):records[pd.Timestamp(row['date'])].append(row)
    fractional=defaultdict(list)
    for row in result.get('corporate_action_audit',[]):
     if row.get('fractional_receivable'):fractional[pd.Timestamp(row['date'])].append(row)
    held=defaultdict(int);before={};previous_nav=ctx.config.start_capital
    monthly={}
    for day,total_nav in nav.items():
     key=day.strftime('%Y-%m')
     if key not in monthly:monthly[key]=dict(basis=previous_nav,end=float(total_nav),date=day.strftime('%Y-%m-%d'),pnl=defaultdict(float))
     month=monthly[key];flow=defaultdict(float)
     for row in records.get(day,[]):
      code=str(row['code']);side=row['side'];qty=int(row['shares'])
      if side=='BUY':held[code]+=qty;flow[code]-=float(row['amount'])+float(row['fee'])
      elif side=='SELL':held[code]-=qty;flow[code]+=float(row['amount'])-float(row['fee'])
      elif side=='SHARE_ADJUST':held[code]+=qty
      elif side=='DIVIDEND':flow[code]+=float(row['amount'])
      if held[code]<0:raise ValueError(f'Monthly attribution negative holding: {day} {code}')
     for row in fractional.get(day,[]):flow[str(row['code'])]+=float(row['fractional_receivable'])
     current={}
     for code,qty in held.items():
      if not qty:continue
      price=float(marks.at[day,code])
      current[code]=qty*price if math.isfinite(price) and price>0 else 0.
     contributions={code:current.get(code,0)-before.get(code,0)+flow.get(code,0) for code in set(current)|set(before)|set(flow)}
     error=abs(sum(contributions.values())-(float(total_nav)-previous_nav))
     if error>max(1e-5,abs(float(total_nav))*1e-10):raise ValueError(f'Monthly attribution does not reconcile: {day} {error}')
     for code,pnl in contributions.items():month['pnl'][code]+=pnl
     month['end']=float(total_nav);month['date']=day.strftime('%Y-%m-%d')
     before=current;previous_nav=float(total_nav)

    names=dict(ctx.name_map or {})
    historical=json.loads(Path(__file__).with_name('historical_names.json').read_text(encoding='utf-8'))
    for code,entry in historical.items():names.setdefault(code,entry['name'])
    output={}
    for key,month in monthly.items():
        rows=[dict(code=code,name=names.get(code,code),contribution=pnl/month['basis']*100)
              for code,pnl in month['pnl'].items() if abs(pnl)>=.005]
        gains=sorted((r for r in rows if r['contribution']>0),key=lambda r:(-r['contribution'],r['code']))
        losses=sorted((r for r in rows if r['contribution']<0),key=lambda r:(r['contribution'],r['code']))
        output[key]=dict(through=month['date'],return_pct=(month['end']/month['basis']-1)*100,
                         winner=gains[0] if gains else None,loser=losses[0] if losses else None)
    return dict(through=nav.index[-1].strftime('%Y-%m-%d'),months=output,
                basis='monthly_strategy_return_contribution_percentage_points')
