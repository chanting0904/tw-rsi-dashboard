/* Full-portfolio integer preview; mirror of v4_engine/allocation.py. */
function previewAllocation(targets, capital, buyFee=.0004) {
  if (!Number.isFinite(capital)||capital<=0||!Number.isFinite(buyFee)||buyFee<0||buyFee>=1) throw new Error('Invalid capital or fee');
  const total=targets.reduce((sum,row)=>sum+Number(row.target_weight??0),0);
  if (!Number.isFinite(total)||total>1+1e-9) throw new Error('Target weights exceed capital');
  const rows=[], quantities={}, missing=[], unaffordable=[], restricted=[];
  for (const row of targets) {
    const code=String(row.code), weight=Number(row.target_weight??0), price=row.price==null?NaN:Number(row.price);
    if (Object.hasOwn(quantities,code)||!Number.isFinite(weight)||weight<0) throw new Error('Invalid target list');
    quantities[code]=0;
    let reason=null;
    if (row.allocation_eligible===false) {restricted.push(code);reason='restricted';}
    else if (weight>0&&(!Number.isFinite(price)||price<=0)) {missing.push(code);reason='missing_price';}
    else if (weight>0&&capital*weight<price) {unaffordable.push(code);reason='under_one_share';}
    rows.push({code,weight,price,excluded:reason,pool:row.channel??'default'});
  }
  const active=rows.filter(row=>row.weight>0&&row.excluded===null).sort((a,b)=>a.code<b.code?-1:a.code>b.code?1:0);
  const pools=[...new Set(active.map(row=>row.pool))],poolWeights={},activeWeights={};
  for (const pool of pools) {
    poolWeights[pool]=rows.filter(row=>row.pool===pool&&row.excluded!=='restricted').reduce((sum,row)=>sum+row.weight,0);
    activeWeights[pool]=active.filter(row=>row.pool===pool).reduce((sum,row)=>sum+row.weight,0);
  }
  const sumWeight=pools.reduce((sum,pool)=>sum+poolWeights[pool],0);
  const budget=active.length ? .95*capital/(1+.95*buyFee) : 0;
  const ideals={};
  for (const row of active) {ideals[row.code]=budget*poolWeights[row.pool]/sumWeight*row.weight/activeWeights[row.pool];quantities[row.code]=Math.floor(ideals[row.code]/row.price);}
  let invested=active.reduce((sum,row)=>sum+quantities[row.code]*row.price,0);
  function headroom() {
    const nav=capital-invested*buyFee;
    return Math.min(Math.max(0,capital-invested*(1+buyFee))/(1+buyFee),Math.max(0,.95*nav-invested)/(1+.95*buyFee));
  }
  if (active.length&&invested/(capital-invested*buyFee)<.90) {
    for (let i=0;i<active.length*3+3;i++) {
      const nav=capital-invested*buyFee;
      if (invested/nav>=.925) break;
      const available=headroom();
      const candidates=active.filter(row=>row.price<=available+1e-9);
      if (!candidates.length) break;
      candidates.sort((a,b)=>{
        const sa=(ideals[a.code]-quantities[a.code]*a.price)/ideals[a.code];
        const sb=(ideals[b.code]-quantities[b.code]*b.price)/ideals[b.code];
        return sb-sa||(a.code<b.code?-1:a.code>b.code?1:0);
      });
      const row=candidates[0], current=quantities[row.code]*row.price;
      const needed=Math.max(0,(.925*nav-invested)/(1+.925*buyFee));
      const gap=Math.max(row.price,ideals[row.code]-current);
      let qty=Math.max(1,Math.floor(Math.min(available,needed,gap)/row.price));
      if (current>=ideals[row.code]) qty=Math.max(1,Math.floor(Math.min(available,needed)/row.price));
      quantities[row.code]+=qty;invested+=qty*row.price;
    }
  }
  const fees=invested*buyFee, cash=capital-invested-fees, nav=capital-fees;
  if (cash< -1e-7||invested/nav>.95+1e-10) throw new Error('Preview violates cash or exposure limit');
  const details=[],weights={};
  for (const row of rows) {
    const {code,price}=row,qty=quantities[code],amount=qty?qty*price:0,target=ideals[code]??capital*row.weight;
    let reason=row.excluded;
    if (reason===null&&row.weight>0) {
      if (qty<Math.floor(capital*row.weight/price)) reason='exposure_limit';
      else if (amount>ideals[code]+1e-8) reason='rounding_repair';
      else if (Math.abs(target-capital*row.weight)>price) reason='redistributed';
      else reason='rounding';
      if (!qty) unaffordable.push(code);
    }
    details.push({code,quantity:qty,target_amount:target,original_target_amount:capital*row.weight,invested:amount,shortfall:Math.max(0,target-amount),reason});
    weights[code]=amount/nav;
  }
  return {quantities,cash:Math.max(0,cash),invested,fees,nav,exposure:invested/nav,
    target_weight_total:active.length ? .95 : 0,original_weight_total:total,unaffordable,missing,restricted,details,weights,
    shortfall:Math.max(0,budget-invested),below_band_amount:Math.max(0,.90*nav-invested),band_reachable:invested/nav>=.90-1e-10&&invested/nav<=.95+1e-10};
}
if (typeof module!=='undefined'&&module.exports) module.exports={previewAllocation};
