(function(root){
  'use strict';
  root.initOrderPlanner=function({data,getTargets,getCapital,money}){
    const $=id=>document.getElementById(id);
    let holdings=null,prices={},lastSides={};
    const sideLabel=row=>row.side==='SELL'&&row.target>0?'減碼':{BUY:'買進',SELL:'賣出',HOLD:'不用動'}[row.side];
    function render(){
      if(holdings===null)return;
      try{
        const capital=getCapital();
        const targets=getTargets();
        const preliminary=OrderPlanner.planOrders({targets,holdings,quotes:data.order_quotes||{},capital,stress:$('order-mode').value==='stress'});
        for(const row of preliminary.rows)if(lastSides[row.code]!==row.side)delete prices[row.code];
        const plan=OrderPlanner.planOrders({targets,holdings,quotes:data.order_quotes||{},capital,prices,stress:$('order-mode').value==='stress',
          commissionRate:data.buy_fee,minimum:data.minimum_commission,saleTax:data.fee_model?.stock_sale_tax??.003});
        lastSides=Object.fromEntries(plan.rows.map(r=>[r.code,r.side]));
        const table=$('order-table');table.replaceChildren();
        const head=table.createTHead().insertRow();
        for(const label of ['股票','動作','這次買賣股數','目前 → 目標','委託價試算','預估扣款／淨入款']){const th=document.createElement('th');th.textContent=label;head.appendChild(th);}
        const body=table.createTBody();
        const sorted=plan.rows.filter(r=>r.current||r.target).sort((a,b)=>({SELL:0,BUY:1,HOLD:2}[a.side]-{SELL:0,BUY:1,HOLD:2}[b.side])||a.code.localeCompare(b.code));
        for(const row of sorted){
          const tr=body.insertRow();if(row.side==='SELL'&&row.target>0)tr.className='is-reduction';tr.insertCell().textContent=row.code+' '+row.name;
          tr.insertCell().textContent=sideLabel(row);tr.insertCell().textContent=Math.abs(row.change)+' 股';
          tr.insertCell().textContent=row.current+' → '+row.target;
          const cell=tr.insertCell();
          if(row.side!=='HOLD'){
            const input=document.createElement('input');input.type='number';input.min='.01';input.step='.01';input.className='order-price';
            input.setAttribute('aria-label',row.code+' 委託價');input.value=Number.isFinite(row.price)&&row.price>0?row.price:'';
            input.addEventListener('change',()=>{prices[row.code]=input.value===''?null:Number(input.value);render();});cell.appendChild(input);
          }else cell.textContent='—';
          const note=document.createElement('div');note.className='note';note.textContent='行情 '+(row.price_date||'缺漏')+'｜狀態核對 '+(row.status_date||'缺漏');cell.appendChild(note);
          tr.insertCell().textContent=row.issues.length?row.issues.join('；'):row.side==='BUY'?money(row.amount+row.fee):row.side==='SELL'?money(row.amount-row.fee):'—';
        }
        const summary=$('order-summary');summary.className='note';
        const text=['買單預留金額（含費用）：'+money(plan.buyCost),'賣單全成交後預估淨收入：'+money(plan.sellNet)];
        if(!plan.complete){text.push('有缺價或交易狀態未確認的項目，上述僅為已可估項目的小計；整份資金檢查尚未通過。');summary.className='warnbox';}
        else if(!plan.budgetComplete){text.push('部分現有持股缺少行情，無法核對總資金；請等待報價補齊。');summary.className='warnbox';}
        else{
          text.push('本次總資金以網頁輸入的 '+money(plan.capital)+' 為準。');
          text.push(plan.withinCapital?'總資金檢查通過：假設清單全部成交，調倉及費用未超過輸入總資金，預估剩餘 '+money(plan.budgetBalance)+'。':plan.netCash<0?'輸入總資金少於目前持股估值，請確認輸入總資金或當次持股。':'委託價壓力估算超出總資金 '+money(-plan.budgetBalance)+'；請調整價格或減少買進股數。');
          if(!plan.withinCapital)summary.className='warnbox';
        }
        summary.textContent=text.join('　');
        $('order-error').textContent='';$('order-results').hidden=false;
        $('order-copy').onclick=async()=>{
          const text='總資金 '+getCapital()+' 元｜'+data.release_version+'｜目標基準 '+data.rebal_date+'\n'+
            sorted.map(r=>r.code+' '+r.name+' '+sideLabel(r)+' '+Math.abs(r.change)+' 股'+(r.issues.length?'（'+r.issues.join('；')+'）':'')).join('\n');
          try{await navigator.clipboard.writeText(text);$('order-copy-note').textContent='已複製';}catch(_){$('order-copy-note').textContent='無法複製，請直接參考下表。';}
        };
      }catch(error){$('order-error').textContent=error.message;$('order-results').hidden=true;}
    }
    $('order-calc').addEventListener('click',()=>{try{holdings=OrderPlanner.parseHoldings($('order-holdings').value);prices={};lastSides={};render();}catch(e){$('order-error').textContent=e.message;$('order-results').hidden=true;holdings=null;}});
    $('order-clear').addEventListener('click',()=>{holdings=null;prices={};lastSides={};$('order-holdings').value='';$('order-results').hidden=true;$('order-error').textContent='';$('order-copy-note').textContent='';});
    $('order-mode').addEventListener('change',()=>{prices={};render();});
    return {render};
  };
})(typeof globalThis!=='undefined'?globalThis:this);
