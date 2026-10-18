/* Personal theoretical plan. Model selection/backtest remain canonical. */
(function(root){
  'use strict';
  const validCapital=n=>Number.isFinite(n)&&n>=10000&&n<=1e12;
  function weeklyCapitalPlan({previous,current,previousCapital,capital,quotes={},snapshot=null}){
    if(!validCapital(previousCapital)||!validCapital(capital))throw Error('上週及本週總資金都須為10,000元至1兆元。');
    if(!previous||!current||previous.rebalance_date>=current.rebalance_date)throw Error('缺少有效的上週模型資料，暫不產生下單清單。');
    const rate=current.buy_fee??.000399,min=current.minimum_commission??1,tax=current.fee_model?.stock_sale_tax??.003;
    if(![rate,min,tax].every(x=>Number.isFinite(x)&&x>=0)||rate>=1||tax>=1)throw Error('費用資料無效。');
    const fee=(amount,sell=false)=>amount>0?Math.max(min,amount*rate)+(sell?amount*tax:0):0;
    const before=(previous.actions||[]).filter(a=>a.allocation_eligible!==false&&a.action!=='SELL'&&a.post_shares>0);
    const priorTargets=before.map(a=>({code:a.stock_code,price:a.price,target_weight:a.post_weight??a.target_weight,channel:a.channel}));
    const reconstructed=root.previewAllocation(priorTargets,previousCapital,previous.buy_fee??rate,previous.minimum_commission??min);
    if(reconstructed.missing.length)throw Error('上週模型缺少股價，無法推算上週股數。');
    let source='reconstructed',held=reconstructed.quantities;
    if(snapshot){
      if(snapshot.schema!==1||snapshot.rebalance_date!==previous.rebalance_date||snapshot.capital!==previousCapital||!Array.isArray(snapshot.holdings))throw Error('上週儲存試算與輸入不符。');
      const known=new Set((previous.actions||[]).map(a=>a.stock_code));held={};
      for(const h of snapshot.holdings){if(!known.has(h.code)||Object.hasOwn(held,h.code)||!Number.isSafeInteger(h.shares)||h.shares<0)throw Error('上週儲存股數無效，請清除試算紀錄後重算。');held[h.code]=h.shares;}
      source='saved';
    }
    const prior=new Map(before.map(a=>[a.stock_code,a]));
    const actions=new Map();
    for(const a of current.actions||[]){if(actions.has(a.stock_code))throw Error('本週模型股票代號重複。');actions.set(a.stock_code,a);}
    const rows=[];
    for(const code of new Set([...Object.keys(held),...actions.keys()])){
      const a=actions.get(code),p=prior.get(code);let shares=held[code]||0;
      if(shares&&!a)throw Error(code+' 缺少本週處置資料，暫不產生下單清單。');
      // Canonical pre-shares are available shares, including verified deliveries/splits.
      if(shares&&p&&a&&p.post_shares>0){const ratio=a.current_shares/p.post_shares;if(!Number.isFinite(ratio)||ratio<=0)throw Error(code+' 股數銜接待確認。');shares=Math.floor(shares*ratio+1e-8);}
      const eligible=a&&a.action!=='SELL'&&a.allocation_eligible!==false;
      if(!shares&&!eligible)continue;
      const q=quotes[code]||{},price=Number(q.price),tradable=q.tradable===true;
      if(!Number.isFinite(price)||price<=0)throw Error(code+' 缺少有效本週股價，暫不產生下單清單。');
      const weight=eligible?Number(a.target_weight):0,ideal=capital*weight;
      if(!Number.isFinite(weight)||weight<0||weight>1)throw Error('本週模型權重無效。');
      const desired=Math.floor(ideal/price);
      let target=shares,reason='續抱不動';
      if(!eligible&&tradable){target=0;reason='退出本週名單';}
      else if(eligible&&!shares&&tradable){target=desired;reason='本週新買';}
      else if(eligible&&tradable){
        const normalIdeal=capital*Number(a.strategy_target_weight??weight);
        if(normalIdeal>0&&shares*price>2*normalIdeal+price){target=Math.floor(normalIdeal/price);reason='超過100%差距門檻';}
      }
      if(!tradable)reason='交易狀態受限，保留股數';
      if(!Number.isSafeInteger(target)||!Number.isSafeInteger(shares))throw Error('股數過大。');
      rows.push({code,name:a.stock_name,channel:a.channel,rank:a.rank,current:shares,target,price,tradable,eligible,ideal,reason});
    }
    const totals=()=>{
      const invested=rows.reduce((s,r)=>s+r.target*r.price,0),fees=rows.reduce((s,r)=>s+fee(Math.abs(r.target-r.current)*r.price,r.target<r.current),0);
      const nav=capital-fees;return {invested,fees,nav,cash:nav-invested,exposure:nav>0?invested/nav:Infinity};
    };
    const fits=()=>{const t=totals();return t.nav>0&&t.cash>=-1e-7&&t.exposure<=.95+1e-12;};
    // Cancel excess new buys before selling retained holdings to meet the ceiling.
    const trim=rows.filter(r=>r.tradable&&r.target>0).sort((a,b)=>(a.current===0?0:1)-(b.current===0?0:1)||(b.target*b.price-b.ideal)-(a.target*a.price-a.ideal)||a.code.localeCompare(b.code));
    for(const r of trim){
      if(fits())break;
      const old=r.target;r.target=0;
      if(!fits()){r.reason='總資金／95%上限調整';continue;}
      let low=0,high=old;
      while(low<high){const mid=Math.ceil((low+high)/2);r.target=mid;if(fits())low=mid;else high=mid-1;}
      r.target=low;r.reason='總資金／95%上限調整';
    }
    if(totals().exposure<.90){
      // Fill new names first; touch held names only when the whole portfolio is below 90%.
      const add=rows.filter(r=>r.eligible&&r.tradable&&r.ideal>r.target*r.price).sort((a,b)=>(a.current===0?0:1)-(b.current===0?0:1)||(b.ideal-b.target*b.price)-(a.ideal-a.target*a.price)||a.code.localeCompare(b.code));
      for(const r of add){
        const t=totals();if(t.exposure>=.925)break;
        const old=r.target,goal=Math.max(old,Math.min(Math.floor(r.ideal/r.price),old+Math.ceil((.925*t.nav-t.invested)/r.price)));
        let low=old,high=goal;
        while(low<high){const mid=Math.ceil((low+high)/2);r.target=mid;if(fits())low=mid;else high=mid-1;}
        r.target=low;if(low!==old)r.reason='整體低於90%，補足配置';
      }
    }
    const result=totals();
    if(![result.invested,result.fees,result.nav,result.cash].every(Number.isFinite))throw Error('試算金額無效。');
    for(const r of rows){r.change=r.target-r.current;r.side=r.change>0?'BUY':r.change<0?'SELL':'HOLD';r.fee=fee(Math.abs(r.change)*r.price,r.change<0);if(!r.change)r.reason=r.tradable?'續抱不動':r.reason;}
    const withinBudget=result.nav>0&&result.cash>=-1e-7&&result.exposure<=.95+1e-10;
    return {...result,rows,capital,previousCapital,source,previousDate:previous.rebalance_date,date:current.rebalance_date,withinBudget,bandReached:withinBudget&&result.exposure>=.90-1e-10,
      snapshot:{schema:1,rebalance_date:current.rebalance_date,capital,holdings:rows.filter(r=>r.target>0).map(r=>({code:r.code,shares:r.target}))}};
  }
  root.weeklyCapitalPlan=weeklyCapitalPlan;
  if(typeof module!=='undefined'&&module.exports)module.exports={weeklyCapitalPlan};
})(typeof globalThis!=='undefined'?globalThis:this);
