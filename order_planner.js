/* Optional, local-only holdings differences and conditional order-price budgets. */
(function (root) {
  'use strict';
  const validCode = c => /^[1-9]\d{3}$/.test(String(c));
  const validQuantity = q => Number.isSafeInteger(q) && q >= 0;
  function parseHoldings(text) {
    text=String(text).trim();
    if (text==='空手') return [];
    if (!text) throw new Error('請輸入每檔「代號 股數」；目前空手請輸入「空手」。');
    const seen=new Set();
    return text.split(/\r?\n/).filter(s=>s.trim()).map((line,i)=>{
      const match=line.trim().match(/^([1-9]\d{3})[\s,，]+(?:(\S+)\s+)?([\d,]+)$/);
      const code=match?.[1], raw=match?.[3]||'', normalized=raw.replace(/,/g,'');
      if (!match || (match[2] && /^\d+$/.test(match[2])) || !/^(?:\d+|\d{1,3}(?:,\d{3})+)$/.test(raw) || !validQuantity(Number(normalized)))
        throw new Error('第'+(i+1)+'行請填「股票代號 股數」，股數須為非負整數，單位是股。');
      if (seen.has(code)) throw new Error(code+' 重複輸入，請合併成一行。');
      seen.add(code);return {code,shares:Number(normalized),name:match[2]||''};
    });
  }
  function bandPrice(reference, side) {
    if (!Number.isFinite(reference) || reference<=0) return null;
    const raw=reference*(side==='BUY'?1.1:.9);
    const tick=raw<10?.01:raw<50?.05:raw<100?.1:raw<500?.5:raw<1000?1:5;
    const units=raw/tick;
    return Math.max(.01,Number(((side==='BUY'?Math.floor(units+1e-8):Math.ceil(units-1e-8))*tick).toFixed(2)));
  }
  function planOrders({targets,holdings,quotes={},cash=null,capital=null,prices={},stress=true,commissionRate=.000399,minimum=1,saleTax=.003}) {
    if (capital!==null && (!Number.isFinite(capital)||capital<=0)) throw new Error('總資金須為正數。');
    if (cash!==null && (!Number.isFinite(cash)||cash<0)) throw new Error('可用現金須為非負金額。');
    if (![commissionRate,minimum,saleTax].every(n=>Number.isFinite(n)&&n>=0) || commissionRate>=1 || saleTax>=1) throw new Error('費率無效。');
    const target=new Map(),held=new Map();
    for (const row of targets) {
      const code=String(row.code);
      if (!validCode(code)||!validQuantity(row.quantity)||target.has(code)) throw new Error('目標股數或代號無效。');
      target.set(code,row);
    }
    for (const row of holdings) {
      const code=String(row.code);
      if (!validCode(code)||!validQuantity(row.shares)||held.has(code)) throw new Error('持股股數或代號無效。');
      held.set(code,row);
    }
    let buyCost=0,sellNet=0,fees=0;
    const rows=[...new Set([...target.keys(),...held.keys()])].sort().map(code=>{
      const t=target.get(code),h=held.get(code),q=quotes[code]||{};
      const current=h?.shares||0,goal=t?.quantity||0,change=goal-current;
      const side=change>0?'BUY':change<0?'SELL':'HOLD';
      const custom=Object.prototype.hasOwnProperty.call(prices,code);
      const base=Number(q.price);
      const price=custom?prices[code]:(side==='HOLD'?base:(stress?bandPrice(base,side):base));
      const issues=[];
      if (side!=='HOLD') {
        if (q.tradable!==true) issues.push('交易狀態待確認或不可交易');
        if (!Number.isFinite(price)||price<=0) issues.push('請補有效委託價');
      }
      const amount=side==='HOLD'||issues.length?0:Math.abs(change)*price;
      if (!Number.isFinite(amount)) throw new Error('交易金額過大。');
      const commission=amount?Math.max(minimum,amount*commissionRate):0;
      const tax=side==='SELL'?amount*saleTax:0,fee=commission+tax;
      if (side==='BUY') buyCost+=amount+fee;
      if (side==='SELL') sellNet+=amount-fee;
      fees+=fee;
      return {code,name:t?.name||q.name||h?.name||code,current,target:goal,change,side,price,
        price_date:q.date||null,status_date:q.status_date||null,custom_price:custom,amount,commission,tax,fee,issues};
    });
    const complete=!rows.some(r=>r.issues.length);
    const valuationMissing=rows.filter(r=>r.current>0 && (!Number.isFinite(Number(quotes[r.code]?.price))||Number(quotes[r.code]?.price)<=0)).map(r=>r.code);
    const currentValue=valuationMissing.length?null:rows.reduce((sum,r)=>sum+r.current*Number(quotes[r.code]?.price||0),0);
    const netCash=capital!==null&&currentValue!==null?capital-currentValue:null;
    const budgetComplete=capital!==null&&complete&&currentValue!==null;
    const requiredCapital=budgetComplete?currentValue-sellNet+buyCost:null;
    const budgetBalance=budgetComplete?capital-requiredCapital:null;
    if ([currentValue,netCash,requiredCapital,budgetBalance].some(n=>n!==null&&!Number.isFinite(n))) throw new Error('資產總金額過大。');
    if (![buyCost,sellNet,fees].every(Number.isFinite)) throw new Error('交易總金額過大。');
    return {rows,buyCost,sellNet,fees,complete,cash,capital,currentValue,netCash,valuationMissing,budgetComplete,requiredCapital,budgetBalance,
      withinCapital:budgetComplete&&netCash>=0&&budgetBalance>=-1e-7,
      canAffordNow:complete&&cash!==null&&cash>=buyCost,
      shortageNow:complete&&cash!==null?Math.max(0,buyCost-cash):null,
      afterSalesBalance:complete&&cash!==null?cash+sellNet-buyCost:null};
  }
  const api={parseHoldings,bandPrice,planOrders};
  if (typeof module!=='undefined'&&module.exports) module.exports=api;
  root.OrderPlanner=api;
})(typeof globalThis!=='undefined'?globalThis:this);
