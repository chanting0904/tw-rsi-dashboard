/* Mirror of v5_engine/allocation.py; minimum commission is per stock order. */
function previewAllocation(targets,capital,buyFee=.000399,minimumCommission=1) {
  if (!Number.isFinite(capital)||capital<=0||capital>1e12||!Number.isFinite(buyFee)||buyFee<0||buyFee>=1||!Number.isFinite(minimumCommission)||minimumCommission<0) throw new Error('Invalid capital or commission');
  targets=[...targets].sort((a,b)=>String(a.code)<String(b.code)?-1:String(a.code)>String(b.code)?1:0);
  const total=targets.reduce((s,r)=>s+Number(r.target_weight??0),0);
  if (!Number.isFinite(total)||total>1+1e-9) throw new Error('Target weights exceed capital');
  const rows=[],quantities={},missing=[],unaffordable=[],restricted=[];
  for (const r of targets) {
    const code=String(r.code),weight=Number(r.target_weight??0),price=r.price==null?NaN:Number(r.price);
    if (Object.hasOwn(quantities,code)||!Number.isFinite(weight)||weight<0) throw new Error('Invalid target list');
    quantities[code]=0;let reason=null;
    if (r.allocation_eligible===false) {restricted.push(code);reason='restricted';}
    else if (weight>0&&(!Number.isFinite(price)||price<=0)) {missing.push(code);reason='missing_price';}
    else if (weight>0&&capital*weight<price) {unaffordable.push(code);reason='under_one_share';}
    rows.push({code,weight,price,excluded:reason,pool:r.channel??'default'});
  }
  const active=rows.filter(r=>r.weight>0&&r.excluded===null),pools=[...new Set(active.map(r=>r.pool))].sort(),poolWeights={},activeWeights={};
  for (const p of pools) {poolWeights[p]=rows.filter(r=>r.pool===p&&r.excluded!=='restricted').reduce((s,r)=>s+r.weight,0);activeWeights[p]=active.filter(r=>r.pool===p).reduce((s,r)=>s+r.weight,0);}
  const sumWeight=pools.reduce((s,p)=>s+poolWeights[p],0),budget=active.length?Math.max(0,.95*(capital-active.length*minimumCommission)/(1+.95*buyFee)):0,ideals={};
  for (const r of active) {ideals[r.code]=budget*poolWeights[r.pool]/sumWeight*r.weight/activeWeights[r.pool];quantities[r.code]=Math.floor(ideals[r.code]/r.price);if(!Number.isSafeInteger(quantities[r.code]))throw new Error('Quantity exceeds safe integer range');}
  const fee=amount=>amount>0?Math.max(minimumCommission,amount*buyFee):0;
  function totals() {const invested=active.reduce((s,r)=>s+quantities[r.code]*r.price,0),fees=active.reduce((s,r)=>s+fee(quantities[r.code]*r.price),0);return {invested,fees,nav:capital-fees};}
  let {invested,fees,nav}=totals();
  function maxAdd(r) {const previous=quantities[r.code]*r.price;let low=0,high=Math.min(Number.MAX_SAFE_INTEGER-quantities[r.code],Math.max(0,Math.floor((capital-invested-fees)/r.price)));if(!Number.isSafeInteger(high))throw new Error('Quantity exceeds safe integer range');while(low<high){const qty=low+Math.ceil((high-low)/2),extra=fee(previous+qty*r.price)-fee(previous);if(invested+qty*r.price+fees+extra<=capital+1e-9&&invested+qty*r.price<=.95*(nav-extra)+1e-9)low=qty;else high=qty-1;}return low;}
  if(active.length&&invested/nav<.90) {
    for(let i=0;i<active.length*3+3;i++) {
      if(invested/nav>=.925)break;
      const candidates=active.filter(r=>maxAdd(r)>0&&ideals[r.code]>0);
      if(!candidates.length)break;
      candidates.sort((a,b)=>{const sa=(ideals[a.code]-quantities[a.code]*a.price)/ideals[a.code],sb=(ideals[b.code]-quantities[b.code]*b.price)/ideals[b.code];return sb-sa||(a.code<b.code?-1:a.code>b.code?1:0);});
      const r=candidates[0],current=quantities[r.code]*r.price,needed=Math.max(0,.925*nav-invested),gap=Math.max(r.price,ideals[r.code]-current);
      let desired=Math.max(1,Math.floor(Math.min(needed,gap)/r.price));if(current>=ideals[r.code])desired=Math.max(1,Math.floor(needed/r.price));
      quantities[r.code]+=Math.min(desired,maxAdd(r));({invested,fees,nav}=totals());
    }
  }
  const cash=capital-invested-fees;if(cash< -1e-7||invested/nav>.95+1e-10)throw new Error('Preview exceeds cash or exposure limit');
  const details=[],weights={};for(const r of rows){const code=r.code,price=r.price,qty=quantities[code],amount=qty?qty*price:0,target=ideals[code]??capital*r.weight;let reason=r.excluded;
    if(reason===null&&r.weight>0){if(qty<Math.floor(capital*r.weight/price))reason='exposure_limit';else if(amount>ideals[code]+1e-8)reason='rounding_repair';else if(Math.abs(target-capital*r.weight)>price)reason='redistributed';else reason='rounding';if(!qty)unaffordable.push(code);}
    details.push({code,quantity:qty,target_amount:target,original_target_amount:capital*r.weight,invested:amount,shortfall:Math.max(0,target-amount),reason,commission:fee(amount)});weights[code]=amount/nav;
  }
  return {quantities,cash:Math.max(0,cash),invested,fees,nav,exposure:invested/nav,target_weight_total:active.length ? .95 : 0,original_weight_total:total,unaffordable,missing,restricted,details,weights,shortfall:Math.max(0,budget-invested),below_band_amount:Math.max(0,.90*nav-invested),minimum_commission:minimumCommission,band_reachable:invested/nav>=.90-1e-10&&invested/nav<=.95+1e-10};
}
if(typeof module!=='undefined'&&module.exports)module.exports={previewAllocation};
