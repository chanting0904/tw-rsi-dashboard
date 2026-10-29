/* Personal theoretical plan. Model selection/backtest remain canonical. */
(function(root){
  'use strict';
  const validCapital=n=>Number.isFinite(n)&&n>=10000&&n<=1e12;
  function weeklyCapitalPlan({previous,current,previousCapital,capital,quotes={},snapshot=null,reservedAssets=null,simulation=false,adjustmentsApplied=false,adjustmentReference=null}){
    if(!(simulation?Number.isFinite(previousCapital)&&previousCapital>0&&previousCapital<=1e12:validCapital(previousCapital))||!(simulation?Number.isFinite(capital)&&capital>0&&capital<=1e12:validCapital(capital)))throw Error('上週及本週總資金都須為10,000元至1兆元。');
    if(!previous||!current||previous.rebalance_date>=current.rebalance_date)throw Error('缺少有效的上週模型資料，暫不產生下單清單。');
    if(reservedAssets===null)reservedAssets=Number(current.receivables||0)*capital/Number(current.capital_basis||current.portfolio_value||capital);
    if(!Number.isFinite(reservedAssets)||reservedAssets<0||reservedAssets>capital)throw Error('不可動用權益資料無效。');
    const rate=current.buy_fee??.000399,min=current.minimum_commission??1,tax=current.fee_model?.stock_sale_tax??.003;
    if(![rate,min,tax].every(x=>Number.isFinite(x)&&x>=0)||rate>=1||tax>=1)throw Error('費用資料無效。');
    const fee=(amount,sell=false)=>amount>0?Math.max(min,amount*rate)+(sell?amount*tax:0):0;
    const before=(previous.actions||[]).filter(a=>a.allocation_eligible!==false&&a.action!=='SELL'&&a.post_shares>0);
    let source='reconstructed',held={};
    if(snapshot){
      if(snapshot.schema!==1||snapshot.rebalance_date!==previous.rebalance_date||snapshot.capital!==previousCapital||!Array.isArray(snapshot.holdings))throw Error('上週儲存試算與輸入不符。');
      const known=new Set((previous.actions||[]).map(a=>a.stock_code));held={};
      for(const h of snapshot.holdings){if(!known.has(h.code)||Object.hasOwn(held,h.code)||!Number.isSafeInteger(h.shares)||h.shares<0)throw Error('上週儲存股數無效，請清除試算紀錄後重算。');held[h.code]=h.shares;}
      source='saved';
    }else{
      // Restore existing shares; do not allocate their historical cash again.
      const publishedCapital=previous.capital_basis;
      if(publishedCapital!==undefined&&(!Number.isFinite(publishedCapital)||publishedCapital<=0))throw Error('上週公布本金基準無效。');
      let totalWeight=0;
      for(const a of before){
        const code=a.stock_code,weight=Number(a.post_weight??a.target_weight),price=Number(a.price);
        if(Object.hasOwn(held,code)||!Number.isFinite(weight)||weight<0||weight>1)throw Error('上週模型配置權重無效。');
        if(weight>0&&(!(price>0)||!Number.isFinite(price)))throw Error('上週模型缺少股價，無法推算上週股數。');
        const shares=Math.floor((publishedCapital!==undefined?previousCapital*a.post_shares/publishedCapital:(weight>0?previousCapital*weight/price:0))+1e-8);
        if(!Number.isSafeInteger(shares))throw Error('上週股數超過安全整數範圍。');
        held[code]=shares;totalWeight+=weight;
      }
      if(totalWeight>1+1e-9)throw Error('上週模型配置權重超過100%。');
    }
    const corporatePrevious=adjustmentReference||previous;
    if(corporatePrevious.rebalance_date!==previous.rebalance_date)throw Error('配股及拆併股銜接日期不一致。');
    const prior=new Map((corporatePrevious.actions||[]).filter(a=>a.post_shares>0).map(a=>[a.stock_code,a]));
    const actions=new Map();
    for(const a of current.actions||[]){if(actions.has(a.stock_code))throw Error('本週模型股票代號重複。');actions.set(a.stock_code,a);}
    const writtenOff=new Set((current.restricted_assets||[]).filter(a=>a.policy==='zero'&&a.book_value===0).map(a=>String(a.code)));
    const priorPending=(snapshot?.pendingHoldings||previous.pending_assets||[]).map(x=>({...x,shares:Number(x.shares)}));
    const pendingKey=x=>String(x.code)+'|'+x.ex_date;
    const currentPending=new Set((current.pending_assets||[]).map(pendingKey));
    const releaseKeys=new Set((current.stock_deliveries||[]).map(pendingKey));
    const released={},modelReleased={};
    if(!adjustmentsApplied){
      for(const x of priorPending){
        if(!Number.isSafeInteger(x.shares)||x.shares<0)throw Error('待領配股股數無效。');
        if(!currentPending.has(pendingKey(x))&&(releaseKeys.has(pendingKey(x))||(x.tradable_date&&x.tradable_date<=current.rebalance_date))){
          released[x.code]=(released[x.code]||0)+x.shares;
          const m=(corporatePrevious.pending_assets||[]).find(y=>pendingKey(y)===pendingKey(x));
          modelReleased[x.code]=(modelReleased[x.code]||0)+Number(m?.shares||0);
        }
      }
    }
    const originalHeld={...held};
    for(const code of Object.keys(released))if(!Object.hasOwn(held,code))held[code]=0;
    const rows=[];
    for(const code of new Set([...Object.keys(held),...actions.keys()])){
      const a=actions.get(code),p=prior.get(code);let shares=held[code]||0;
      // A confirmed zero valuation is a write-off, never a sale or cash receipt.
      if(writtenOff.has(String(code))&&a?.allocation_eligible!==true&&!(a?.action==='SELL'&&a.estimated_sell_amount>0))continue;
      if(shares&&!a)throw Error(code+' 缺少本週處置資料，暫不產生下單清單。');
      // Canonical pre-shares are available shares, including verified deliveries/splits.
      if(!adjustmentsApplied&&shares&&p&&a&&p.post_shares>0){const ratio=(a.current_shares-(modelReleased[code]||0))/p.post_shares;if(!Number.isFinite(ratio)||ratio<=0)throw Error(code+' 股數銜接待確認。');shares=Math.floor(shares*ratio+1e-8);}
      shares+=released[code]||0;
      const eligible=a&&a.action!=='SELL'&&a.allocation_eligible!==false;
      if(!shares&&!eligible)continue;
      const q=quotes[code]||{},price=Number(q.price),tradable=q.tradable===true;
      if(!Number.isFinite(price)||price<=0)throw Error(code+' 缺少有效本週股價，暫不產生下單清單。');
      const weight=eligible?Number(a.planner_target_weight??a.target_weight):0;
      if(!Number.isFinite(weight)||weight<0||weight>1)throw Error('本週模型權重無效。');
      const normalIdeal=capital*Number(a?.strategy_target_weight??weight);
      if(!Number.isFinite(normalIdeal)||normalIdeal<0)throw Error('本週策略目標無效。');
      const thresholdTolerance=Math.max(1e-7,Math.abs(normalIdeal)*Number.EPSILON*8);
      let ideal=capital*weight;
      // Do not buy/top up into a position that would immediately require a 100% trim.
      if(normalIdeal>0&&ideal>2*normalIdeal+thresholdTolerance)ideal=normalIdeal;
      const buyable=tradable&&q.buyable!==false,sellable=tradable&&q.sellable!==false;
      const desired=Math.floor(ideal/price);
      if(!Number.isSafeInteger(desired))throw Error('股數超過安全整數範圍，請確認價格與資金。');
      let target=shares,reason='續抱不動';
      if(!eligible&&sellable){target=0;reason='退出本週名單';}
      else if(eligible&&!shares&&buyable){target=desired;reason='本週新買';}
      else if(eligible&&sellable){
        if(normalIdeal>0&&shares*price>2*normalIdeal+thresholdTolerance){target=Math.floor(normalIdeal/price);reason='超過100%差距門檻';}
      }
      if(!tradable)reason='交易狀態受限，保留股數';
      if(!Number.isSafeInteger(target)||!Number.isSafeInteger(shares))throw Error('股數過大。');
      rows.push({code,name:a.stock_name,channel:a.channel,rank:a.rank,current:shares,target,price,tradable,buyable,sellable,eligible,ideal,reason});
    }
    rows.sort((a,b)=>a.code.localeCompare(b.code));
    const totals=()=>{
      const invested=rows.reduce((s,r)=>s+r.target*r.price,0),fees=rows.reduce((s,r)=>s+fee(Math.abs(r.target-r.current)*r.price,r.target<r.current),0);
      const nav=capital-fees;return {invested,fees,nav,cash:nav-invested-reservedAssets,reservedAssets,exposure:nav>0?invested/nav:Infinity};
    };
    const fits=()=>{const t=totals();return t.nav>0&&t.cash>=-1e-7&&t.exposure<=.95+1e-12;};
    // Cancel excess new buys before selling retained holdings to meet the ceiling.
    const trim=rows.filter(r=>r.target>0&&(r.sellable||r.target>r.current)).sort((a,b)=>(a.current===0?0:1)-(b.current===0?0:1)||(b.target*b.price-b.ideal)-(a.target*a.price-a.ideal)||a.code.localeCompare(b.code));
    for(const r of trim){
      if(fits())break;
      const old=r.target,floor=r.sellable?0:r.current;r.target=floor;
      if(!fits()){r.reason='總資金／95%上限調整';continue;}
      let low=floor,high=old;
      while(low<high){const mid=low+Math.ceil((high-low)/2);r.target=mid;if(fits())low=mid;else high=mid-1;}
      r.target=low;r.reason='總資金／95%上限調整';
    }
    if(totals().exposure<.90-1e-12){
      // Fill new names first; touch held names only when the whole portfolio is below 90%.
      const add=rows.filter(r=>r.eligible&&r.buyable&&r.ideal>r.target*r.price).sort((a,b)=>(a.current===0?0:1)-(b.current===0?0:1)||(b.ideal-b.target*b.price)-(a.ideal-a.target*a.price)||a.code.localeCompare(b.code));
      for(const r of add){
        const t=totals();if(t.exposure>=.925-1e-12)break;
        const old=r.target,goal=Math.max(old,Math.min(Math.floor(r.ideal/r.price),old+Math.ceil((.925*t.nav-t.invested)/r.price)));
        let low=old,high=goal;
        while(low<high){const mid=low+Math.ceil((high-low)/2);r.target=mid;if(fits())low=mid;else high=mid-1;}
        r.target=low;if(low!==old)r.reason='整體低於90%，補足配置';
      }
    }
    const result=totals();
    if(![result.invested,result.fees,result.nav,result.cash].every(Number.isFinite))throw Error('試算金額無效。');
    for(const r of rows){r.change=r.target-r.current;r.side=r.change>0?'BUY':r.change<0?'SELL':'HOLD';r.fee=fee(Math.abs(r.change)*r.price,r.change<0);if(!r.change)r.reason=r.tradable?'續抱不動':r.reason;}
    const pendingHoldings=(current.pending_assets||[]).map(x=>{
      const previousPending=priorPending.find(y=>pendingKey(y)===pendingKey(x));
      let shares=previousPending?.shares;
      if(shares===undefined){
        if(x.ex_date>previous.rebalance_date&&Number.isFinite(x.grant_ratio))shares=Math.floor((originalHeld[x.code]||0)*(x.grant_ratio-1)+1e-8);
        else shares=Math.floor(Number(x.shares)*previousCapital/Number(previous.capital_basis||previous.portfolio_value||previousCapital)+1e-8);
      }
      return {code:String(x.code),shares,ex_date:x.ex_date,tradable_date:x.tradable_date||null};
    }).filter(x=>x.shares>0);
    const withinBudget=result.nav>0&&result.cash>=-1e-7&&result.exposure<=.95+1e-10;
    return {...result,rows,capital,previousCapital,source,previousDate:previous.rebalance_date,date:current.rebalance_date,withinBudget,bandReached:withinBudget&&result.exposure>=.90-1e-10,
      snapshot:{schema:1,rebalance_date:current.rebalance_date,capital,pendingHoldings,holdings:rows.filter(r=>r.target>0).map(r=>({code:r.code,shares:r.target}))}};
  }
  root.weeklyCapitalPlan=weeklyCapitalPlan;
  if(typeof module!=='undefined'&&module.exports)module.exports={weeklyCapitalPlan};
})(typeof globalThis!=='undefined'?globalThis:this);
