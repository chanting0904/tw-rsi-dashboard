"""Whole-share target preview with a commission minimum per stock order."""
import math
from .fees import order_cost


def ordered_sum(values):
    """Match JavaScript reduce order instead of Python's compensated sum."""
    total=0.
    for value in values:total+=value
    return total


def preview_allocation(targets, capital, buy_fee=.000399, minimum_commission=1.):
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError('Invalid capital')
    order_cost(0., 'BUY', buy_fee, minimum_commission)
    targets = sorted(targets, key=lambda r:str(r['code']))
    total = ordered_sum(float(row.get('target_weight',0)) for row in targets)
    if not math.isfinite(total) or total > 1+1e-9:
        raise ValueError('Target weights exceed available capital')
    rows, quantities, missing, unaffordable, restricted = [], {}, [], [], []
    for row in targets:
        code, weight = str(row['code']), float(row.get('target_weight',0))
        if code in quantities or not math.isfinite(weight) or weight < 0:
            raise ValueError('Invalid target list')
        quantities[code] = 0
        price = float(row['price']) if row.get('price') is not None else float('nan')
        reason = None
        if row.get('allocation_eligible') is False:
            restricted.append(code); reason='restricted'
        elif weight > 0 and (not math.isfinite(price) or price <= 0):
            missing.append(code); reason='missing_price'
        elif weight > 0 and capital*weight < price:
            unaffordable.append(code); reason='under_one_share'
        rows.append({'code':code,'weight':weight,'price':price,'excluded':reason,'pool':row.get('channel','default')})
    active=[r for r in rows if r['weight']>0 and r['excluded'] is None]
    pools=sorted({r['pool'] for r in active})
    pool_weights={p:ordered_sum(r['weight'] for r in rows if r['pool']==p and r['excluded']!='restricted') for p in pools}
    active_weights={p:ordered_sum(r['weight'] for r in active if r['pool']==p) for p in pools}
    sum_weight=ordered_sum(pool_weights.values())
    budget=max(0.,.95*(capital-len(active)*minimum_commission)/(1+.95*buy_fee)) if active else 0.
    ideals={r['code']:budget*pool_weights[r['pool']]/sum_weight*r['weight']/active_weights[r['pool']] for r in active}
    for r in active:quantities[r['code']]=math.floor(ideals[r['code']]/r['price'])
    def fee(amount):return order_cost(amount,'BUY',buy_fee,minimum_commission)['fee']
    def totals():
        invested=ordered_sum(quantities[r['code']]*r['price'] for r in active)
        fees=ordered_sum(fee(quantities[r['code']]*r['price']) for r in active)
        return invested,fees,capital-fees
    invested,fees,nav=totals()
    def max_add(row):
        code,price=row['code'],row['price']
        previous=quantities[code]*price
        low,high=0,max(0,math.floor((capital-invested-fees)/price))
        while low<high:
            qty=(low+high+1)//2
            extra=fee(previous+qty*price)-fee(previous)
            if invested+qty*price+fees+extra<=capital+1e-9 and invested+qty*price<=.95*(nav-extra)+1e-9:low=qty
            else:high=qty-1
        return low
    if active and invested/nav<.90:
        for _ in range(len(active)*3+3):
            if invested/nav>=.925:break
            candidates=[r for r in active if max_add(r)>0 and ideals[r['code']]>0]
            if not candidates:break
            row=min(candidates,key=lambda r:(-(ideals[r['code']]-quantities[r['code']]*r['price'])/ideals[r['code']],r['code']))
            current=quantities[row['code']]*row['price']
            needed=max(0.,.925*nav-invested)
            gap=max(row['price'],ideals[row['code']]-current)
            desired=max(1,math.floor(min(needed,gap)/row['price']))
            if current>=ideals[row['code']]:desired=max(1,math.floor(needed/row['price']))
            quantities[row['code']]+=min(desired,max_add(row))
            invested,fees,nav=totals()
    cash=capital-invested-fees
    if cash < -1e-7 or invested/nav>.95+1e-10:raise ValueError('Preview exceeds cash or exposure limit')
    details=[]
    for row in rows:
        code,price=row['code'],row['price'];qty=quantities[code];amount=qty*price if qty else 0.
        target=ideals.get(code,capital*row['weight']);reason=row['excluded']
        if reason is None and row['weight']>0:
            if qty<math.floor(capital*row['weight']/price):reason='exposure_limit'
            elif amount>ideals[code]+1e-8:reason='rounding_repair'
            elif abs(target-capital*row['weight'])>price:reason='redistributed'
            else:reason='rounding'
            if qty==0:unaffordable.append(code)
        details.append({'code':code,'quantity':qty,'target_amount':target,'original_target_amount':capital*row['weight'],
                        'invested':amount,'shortfall':max(0.,target-amount),'reason':reason,'commission':fee(amount)})
    return {'quantities':quantities,'cash':max(0.,cash),'invested':invested,'fees':fees,'nav':nav,'exposure':invested/nav,
            'target_weight_total':.95 if active else 0.,'original_weight_total':total,'unaffordable':unaffordable,
            'missing':missing,'restricted':restricted,'details':details,
            'weights':{r['code']:(quantities[r['code']]*r['price']/nav if quantities[r['code']] else 0.) for r in rows},
            'shortfall':max(0.,budget-invested),'below_band_amount':max(0.,.90*nav-invested),
            'minimum_commission':minimum_commission,'band_reachable':.90-1e-10<=invested/nav<=.95+1e-10}
