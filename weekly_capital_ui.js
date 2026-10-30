(function(root){
  'use strict';
  const pricePolicy='signal_close_v2';
  function snapshotBasis(signal){
    return JSON.stringify({date:signal.rebalance_date,basis:signal.price_basis_date,capital:[signal.capital_basis,signal.portfolio_value],
      fees:[signal.buy_fee,signal.minimum_commission,signal.fee_model?.stock_sale_tax],receivables:signal.receivables,
      actions:(signal.actions||[]).map(a=>[a.stock_code,a.action,a.price,a.price_date,a.price_kind,a.post_shares,a.current_shares,
        a.post_weight,a.target_weight,a.planner_target_weight,a.strategy_target_weight,a.allocation_eligible,a.trading_status,a.buy_allowed,a.sell_allowed])
        .sort((a,b)=>String(a[0]).localeCompare(String(b[0]))),
      pending:signal.pending_assets||[],deliveries:signal.stock_deliveries||[],restricted:signal.restricted_assets||[]});
  }
  function signalQuotes(signal){
    return Object.fromEntries((signal.actions||[]).map(a=>[a.stock_code,{price:a.price,date:a.price_date||signal.price_basis_date,
      tradable:a.trading_status===undefined||['TRADABLE','OUTSIDE_TARGET'].includes(a.trading_status),buyable:a.buy_allowed!==false,sellable:a.sell_allowed!==false}]));
  }
  root.initWeeklyCapital=function({data,money,isReady=()=>true,onChange=()=>{}}){
    const $=id=>document.getElementById(id),key='v4_weekly_capital_snapshots_v1';
    let plan=null,saved={};
    try{saved=JSON.parse(localStorage.getItem(key)||'{}');if(!saved||Array.isArray(saved)||typeof saved!=='object')saved={};}catch(_){saved={};}
    // One week may be tried with several amounts; keep each so the amount actually used can be carried.
    const savedEntries=date=>{const e=saved[date];return !e?[]:Array.isArray(e.byCapital)?e.byCapital:[e];};

    function resetSummary(){
      for(const id of ['preview-buy-count','preview-sell-count','preview-exposure','preview-cash'])$(id).textContent='—';
      $('preview-result-note').textContent='輸入兩週總資金後，查看這次需要買賣的股數。';
    }
    function renderOverview(value,rows){
      $('preview-buy-count').textContent=rows.filter(r=>r.side==='BUY').length+' 檔';
      $('preview-sell-count').textContent=rows.filter(r=>r.side==='SELL').length+' 檔';
      $('preview-exposure').textContent=(value.exposure*100).toFixed(2)+'%';
      $('preview-cash').textContent=money(value.cash);
      $('preview-result-note').textContent='依本週 '+money(value.capital)+' 試算 · 不用動 '+rows.filter(r=>r.side==='HOLD').length+' 檔 · 預估費稅 '+money(value.fees)+(value.reservedAssets>0?' · 待領權益 '+money(value.reservedAssets):'')+(!value.bandReached?' · 配置未達90%，差額保留現金':'');
    }
    function renderGroupedRows(rows){
      const rootTable=$('weekly-capital-table');rootTable.replaceChildren();
      for(const group of [{side:'SELL',label:'賣出／減碼',cls:'sell'},{side:'BUY',label:'買進／加碼',cls:'buy'},{side:'HOLD',label:'續抱不用動',cls:'hold'}]){
        const list=rows.filter(r=>r.side===group.side),box=document.createElement('details');box.className='order-group '+group.cls;box.open=group.side!=='HOLD';
        const heading=document.createElement('summary'),name=document.createElement('span'),count=document.createElement('span');name.className='group-name';name.textContent=group.label;count.className='group-count';count.textContent=list.length+' 檔';heading.append(name,count);box.appendChild(heading);
        if(!list.length){const empty=document.createElement('p');empty.className='group-empty';empty.textContent='本週沒有這類操作';box.appendChild(empty);rootTable.appendChild(box);continue;}
        const table=document.createElement('table'),head=table.createTHead().insertRow();for(const text of ['股票','本次下單','原有 → 調整後','原因']){const th=document.createElement('th');th.textContent=text;head.appendChild(th);}
        const body=table.createTBody();
        for(const r of list){const tr=body.insertRow(),stock=tr.insertCell(),code=document.createElement('span'),name=document.createElement('strong');if(r.side==='SELL'&&r.target>0)tr.className='is-reduction';code.className='stock-code';code.textContent=r.code;name.className='stock-name';name.textContent=r.name;stock.append(code,name);
          const quantity=tr.insertCell(),number=document.createElement('strong'),label=document.createElement('small');quantity.className='order-quantity';number.textContent=r.side==='HOLD'?'—':Math.abs(r.change)+' 股';label.textContent=r.side==='BUY'?(r.current?'加碼':'新買'):r.side==='SELL'?(r.target?'減碼':'賣出'):'不用動';quantity.append(number,label);
          const bridge=tr.insertCell();bridge.className='share-bridge';bridge.textContent=r.current+' → '+r.target+' 股';
          const reason=tr.insertCell(),details=document.createElement('details'),summary=document.createElement('summary'),text=document.createElement('p');details.className='row-reason';summary.textContent='查看';text.textContent=r.reason;details.append(summary,text);reason.appendChild(details);
        }
        box.appendChild(table);rootTable.appendChild(box);
      }
    }
    function invalidate(){plan=null;resetSummary();$('weekly-capital-copy-note').textContent='';$('weekly-capital-results').hidden=true;$('weekly-capital-error').textContent='請確認兩週總資金後，按「計算本週買賣」。';onChange();}
    function calculate(){
      plan=null;resetSummary();$('weekly-capital-results').hidden=true;$('weekly-capital-copy-note').textContent='';
      try{
        if(!data.sig_validated||!isReady())throw Error('本週訊號尚未確認完成，暫不產生買賣清單；請先確認頁首狀態。');
        const previous=data.previous_capital_signal,current=data.capital_signal;
        const adjustmentReference=data.previous_model_signal||previous;
        const previousCapital=Number($('cap-previous').value),capital=Number($('cap').value);
        const entry=savedEntries(previous?.rebalance_date).find(x=>x.capital===previousCapital);
        const snapshot=entry&&entry.price_policy===pricePolicy&&entry.signal_basis===snapshotBasis(previous)?entry:null;
        const result=root.weeklyCapitalPlan({previous,current,previousCapital,capital,quotes:data.order_quotes||{},snapshot,adjustmentReference});
        if(!result.withinBudget)throw Error('受限持股或費用使配置超出本週資金／95%上限，已停止產生可執行清單。');
        const anchor=root.weeklyCapitalPlan({previous,current,previousCapital,capital,quotes:signalQuotes(current),snapshot,adjustmentReference});
        if(!anchor.withinBudget)throw Error('訊號基準配置超出資金／95%上限，暫不產生可承接的清單。');
        // Show the signal-basis plan: the list on screen is exactly what next week carries.
        plan=anchor;
        const rows=[...plan.rows].filter(r=>r.current||r.target).sort((a,b)=>a.code.localeCompare(b.code));
        renderGroupedRows(rows);
        renderOverview(plan,rows);$('weekly-capital-summary').textContent='';
        $('weekly-capital-basis').textContent='上週基準 '+plan.previousDate+' → 本週 '+plan.date+'；'+(plan.source==='saved'?'原有股數沿用本瀏覽器上週用 '+money(plan.previousCapital)+' 算出的清單，與上週畫面相同。':'⚠ 注意：這台瀏覽器找不到上週用 '+money(plan.previousCapital)+' 算出的清單'+(entry?'（上週資料已更新）':'')+'，原有股數改依上週模型推算，可能與上週畫面差幾股；請先對照券商庫存，不同時在下方輸入實際持股核對。')+' 本週清單一律用訊號基準價計算，之後股價漲跌不會改變清單或原有股數，下週會原樣承接。這是理論持股，未連接券商庫存；有未成交或實際股數不同，請在下方輸入完整實際持股，重新核對下單股數。';
        $('weekly-capital-error').textContent='';$('weekly-capital-results').hidden=false;
        const record={...anchor.snapshot,price_policy:pricePolicy,signal_basis:snapshotBasis(current)};
        saved[plan.date]={byCapital:[...savedEntries(plan.date).filter(x=>x.capital!==record.capital),record].slice(-5)};
        saved=Object.fromEntries(Object.entries(saved).sort(([a],[b])=>a.localeCompare(b)).slice(-6));
        try{localStorage.setItem(key,JSON.stringify(saved));}catch(_){$('weekly-capital-error').textContent='本次計算有效，但瀏覽器無法儲存股數；下次將依模型推算。';}
        onChange();
      }catch(error){plan=null;resetSummary();$('weekly-capital-error').textContent=error.message;onChange();}
    }
    $('cap-previous').addEventListener('input',invalidate);$('cap').addEventListener('input',invalidate);
    $('cap-apply').addEventListener('click',calculate);
    $('cap-reset').addEventListener('click',()=>{$('cap-previous').value='';invalidate();});
    $('weekly-capital-clear').addEventListener('click',()=>{saved={};try{localStorage.removeItem(key);}catch(_){}invalidate();$('weekly-capital-error').textContent='已清除理論股數紀錄；下次依上週模型重新推算。';});
    $('weekly-capital-copy').addEventListener('click',async()=>{
      if(!plan)return;
      const lines=plan.rows.filter(r=>r.change).sort((a,b)=>(a.side==='SELL'?0:1)-(b.side==='SELL'?0:1)||a.code.localeCompare(b.code)).map(r=>r.code+' '+r.name+' '+(r.side==='BUY'?'買進':r.target>0?'減碼':'賣出')+' '+Math.abs(r.change)+' 股');
      try{await navigator.clipboard.writeText('理論調倉 '+plan.date+'｜上週 '+plan.previousCapital+' 元｜本週 '+plan.capital+' 元\n'+(lines.join('\n')||'本週不用買賣'));$('weekly-capital-copy-note').textContent='已複製';}catch(_){$('weekly-capital-copy-note').textContent='無法複製，請參考下表。';}
    });
    return {validateFreshness:()=>{if(plan&&!isReady()){invalidate();$('weekly-capital-error').textContent='本週訊號狀態已改變，舊清單已隱藏；請等待確認後重新計算。';}},getTargets:()=>plan?plan.rows.map(r=>({code:r.code,name:r.name,quantity:r.target})):null};
  };
})(typeof globalThis!=='undefined'?globalThis:this);
