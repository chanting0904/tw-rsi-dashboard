"""Raw-price positions, explicit cash entitlements and quantity-changing events.

Dividends are assets from ex date, but cannot fund trades before payment.
Stock grants use ex-date delivery as an explicit simulation approximation.
Unidentified adjusted-price jumps fail closed when held, never become profit.
"""
import re
import math
import pandas as pd


def number(value):
    value = pd.to_numeric(value, errors='coerce')
    return float(value) if pd.notna(value) else 0.


def build_events(announcements, close, adjusted, official_cash=None, stock_grants=None, splits=None, reductions=None):
    events = {}
    def event(day, code):
        return events.setdefault(pd.Timestamp(day).normalize(), {}).setdefault(str(code), {})
    ann = announcements.copy()
    # One final record per ex date, not one payment per duplicate disclosure.
    ann = ann.sort_values('公告日期', kind='stable')
    cash_rows, share_rows = {}, {}
    for row in ann.to_dict('records'):
        code = str(row.get('symbol', row.get('stock_id')))
        if code not in close.columns:
            continue
        cash = number(row.get('盈餘分配之股東現金股利(元/股)')) + number(row.get('法定盈餘公積、資本公積發放之現金(元/股)'))
        ex = pd.to_datetime(row.get('除息交易日'), errors='coerce')
        if cash > 0 and pd.notna(ex):
            payment = pd.to_datetime(row.get('現金股利發放日'), errors='coerce')
            cash_rows[(ex.normalize(),code)] = (cash, payment.normalize() if pd.notna(payment) else None)
        stock = number(row.get('盈餘轉增資配股(元/股)')) + number(row.get('法定盈餘公積、資本公積轉增資配股(元/股)'))
        ex = pd.to_datetime(row.get('除權交易日'), errors='coerce')
        if stock > 0 and pd.notna(ex):
            par = re.search(r'(\d+(?:\.\d+)?)', str(row.get('普通股每股面額','')))
            if par and float(par[1]) > 0:
                share_rows[(ex.normalize(),code)] = 1 + stock / float(par[1])
    for (day,code),(cash,payment) in cash_rows.items():
        event(day,code).update(cash=cash,payment=payment)
    for (day,code),ratio in share_rows.items():
        event(day,code).update(ratio=ratio,kind='STOCK_DIVIDEND')
    for frame,kind in [(official_cash,'cash'),(stock_grants,'stock'),(splits,'split')]:
        if frame is None:
            continue
        frame = frame.reindex(columns=close.columns)
        for day,row in frame.iterrows():
            for code,value in row.items():
                if pd.isna(value) or number(value) <= 0:
                    continue
                item=event(day,code)
                if kind=='cash' and 'cash' not in item:
                    item.update(cash=number(value),payment=None)
                elif kind=='stock':
                    item.update(ratio=1+number(value)/1000,kind='STOCK_DIVIDEND')
                elif kind=='split':
                    item.update(ratio=number(value),kind='SPLIT')
    for reduction in reductions or []:
        reasons,before,reference=reduction
        for date,row in reasons.reindex(columns=close.columns).iterrows():
            for code,reason in row.items():
                if pd.isna(reason) or str(reason) in ('nan','NaN',''):
                    continue
                prior=number(before.get(code,pd.Series(dtype=float)).get(date))
                ref=number(reference.get(code,pd.Series(dtype=float)).get(date))
                if prior<=0 or ref<=0:continue
                item=event(date,code)
                if '退還' in str(reason) or '現金' in str(reason):
                    # Exchange reference uses (old price - par * cancelled fraction) / retained fraction.
                    matching=ann[(ann['symbol'].astype(str)==code)&(ann['公告日期']<=date)]
                    partext=str(matching.iloc[-1].get('普通股每股面額','10')) if len(matching) else '10'
                    parsed=re.search(r'(\d+(?:\.\d+)?)',partext)
                    par=float(parsed[1]) if parsed else 10.
                    if abs(ref-par)<1e-6:continue
                    ratio=round((prior-par)/(ref-par),3)
                    if not 0<ratio<1 or abs((prior-par*(1-ratio))/ratio-ref)>.10:continue
                    item.update(ratio=ratio,cash=par*(1-ratio),payment=None,kind='CASH_CAPITAL_REDUCTION')
                elif '虧損' in str(reason):
                    item.update(ratio=round(prior/ref,3),kind='LOSS_CAPITAL_REDUCTION')
    # Typhoon/unscheduled exchange closures can postpone an announced ex date.
    # Move to the first actual market session; never silently drop an entitlement.
    sessions=close.dropna(how='all').index
    aligned={}
    for date,codes in events.items():
        i=sessions.searchsorted(date)
        effective=sessions[i] if i<len(sessions) else date
        for code,item in codes.items():
            aligned.setdefault(effective,{}).setdefault(code,{}).update(item)
    events=aligned
    # Price-factor change identifies split/face-value events absent announcements.
    # Cash-only adjustments are removed first; unexplained changes are recorded.
    raw_previous=close.ffill().shift(1)
    factors=(adjusted/close).ffill()
    changes=factors/factors.shift(1)
    changed=(changes.sub(1).abs()>1e-7)&changes.notna()
    for day,code in zip(*changed.to_numpy().nonzero()):
        date,code=changes.index[day],changes.columns[code]
        item=event(date,code)
        prev=number(raw_previous.at[date,code])
        cash=number(item.get('cash'))
        implied=number(changes.at[date,code])*(1-cash/prev) if prev else 1.
        known=number(item.get('ratio',1))
        if abs(implied-known) < .005:
            continue
        # Recognize common stock splits/consolidations, not arbitrary price moves.
        candidates=[.1,.2,.25,.5,2.,4.,5.,10.]
        match=min(candidates,key=lambda x:abs(x-implied))
        if abs(implied-match) < .003 and 'ratio' not in item:
            item.update(ratio=match,kind='SPLIT_FACTOR')
        else:
            item['unresolved_factor']=implied
    return events


class CorporateLedger:
    def __init__(self, events):
        self.events=events or {}
        self.receivables=[]
        self.audit=[]

    @property
    def value(self):
        return sum(row['amount'] for row in self.receivables)

    def advance(self, day, positions, cash, prices):
        records=[]
        for code,event in self.events.get(day,{}).items():
            if code not in positions:
                continue
            if 'unresolved_factor' in event:
                raise ValueError(f'公司行動尚未對帳：{day.date()} {code} factor={event["unresolved_factor"]:.6f}')
            pos=positions[code]
            shares=pos['shares']
            if event.get('cash',0)>0:
                amount=shares*event['cash']
                payment=event.get('payment')
                self.receivables.append({'code':code,'amount':amount,'payment':payment})
                records.append({'date':day,'code':code,'pool':pos['pool'],'side':'DIVIDEND',
                    'shares':0,'price':0.,'amount':amount,'reason':'CASH_ENTITLEMENT','full':False,'is_new':False})
                self.audit.append({'date':str(day.date()),'code':code,'kind':'CASH_ENTITLEMENT',
                    'amount':amount,'payment':str(payment.date()) if payment is not None else None})
            ratio=event.get('ratio',1.)
            if abs(ratio-1)>1e-8:
                exact=shares*ratio
                new=int(math.floor(exact+1e-8))
                remainder=exact-new
                price=number(prices.get(code))
                if remainder and price<=0:
                    raise ValueError(f'無法估值畸零股權益 {day} {code}')
                if remainder:
                    self.receivables.append({'code':code,'amount':remainder*price,'payment':None})
                pos['shares']=new
                pos['avg']=pos['avg']/ratio
                records.append({'date':day,'code':code,'pool':pos['pool'],'side':'SHARE_ADJUST',
                    'shares':new-shares,'price':0.,'amount':0.,'reason':event.get('kind','SHARE_ADJUST'),
                    'full':False,'is_new':False})
                self.audit.append({'date':str(day.date()),'code':code,'kind':event.get('kind'),
                    'old_shares':shares,'new_shares':new,'fractional_receivable':remainder*price})
        unsettled=[]
        for row in self.receivables:
            if row['payment'] is not None and row['payment']<=day:
                cash+=row['amount']
            else:
                unsettled.append(row)
        self.receivables=unsettled
        return cash,records
