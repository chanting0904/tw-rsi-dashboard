(function(root){
  'use strict';
  root.initWeeklyCapital=function({data,money,isReady=()=>true,onChange=()=>{}}){
    const $=id=>document.getElementById(id),key='v4_weekly_capital_snapshots_v1';
    let plan=null,saved={};
    try{saved=JSON.parse(localStorage.getItem(key)||'{}');if(!saved||Array.isArray(saved)||typeof saved!=='object')saved={};}catch(_){saved={};}

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
        for(const r of list){const tr=body.insertRow(),stock=tr.insertCell(),code=document.createElement('span'),name=document.createElement('strong');code.className='stock-code';code.textContent=r.code;name.className='stock-name';name.textContent=r.name;stock.append(code,name);
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
        const previousCapital=Number($('cap-previous').value),capital=Number($('cap').value);
        const entry=saved[previous?.rebalance_date];
        const snapshot=entry?.capital===previousCapital?entry:null;
        const result=root.weeklyCapitalPlan({previous,current,previousCapital,capital,quotes:data.order_quotes||{},snapshot});
        if(!result.withinBudget)throw Error('受限持股或費用使配置超出本週資金／95%上限，已停止產生可執行清單。');
        plan=result;
        const rows=[...plan.rows].filter(r=>r.current||r.target).sort((a,b)=>a.code.localeCompare(b.code));
        renderGroupedRows(rows);
        renderOverview(plan,rows);$('weekly-capital-summary').textContent='';
        $('weekly-capital-basis').textContent='上週基準 '+plan.previousDate+' → 本週 '+plan.date+'；'+(plan.source==='saved'?'沿用本瀏覽器已儲存的上週試算股數。':'首次或上週資金不同：依上週模型配置、上週股價及輸入資金推算股數。')+' 本週依各檔頁面基準行情估算，實際成交價會改變費用及剩餘資金；這是理論持股，未連接券商庫存。';
        $('weekly-capital-error').textContent='';$('weekly-capital-results').hidden=false;
        saved[plan.date]=plan.snapshot;
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
      const lines=plan.rows.filter(r=>r.change).sort((a,b)=>(a.side==='SELL'?0:1)-(b.side==='SELL'?0:1)||a.code.localeCompare(b.code)).map(r=>r.code+' '+r.name+' '+(r.side==='BUY'?'買進':'賣出')+' '+Math.abs(r.change)+' 股');
      try{await navigator.clipboard.writeText('理論調倉 '+plan.date+'｜上週 '+plan.previousCapital+' 元｜本週 '+plan.capital+' 元\n'+(lines.join('\n')||'本週不用買賣'));$('weekly-capital-copy-note').textContent='已複製';}catch(_){$('weekly-capital-copy-note').textContent='無法複製，請參考下表。';}
    });
    return {validateFreshness:()=>{if(plan&&!isReady()){invalidate();$('weekly-capital-error').textContent='本週訊號狀態已改變，舊清單已隱藏；請等待確認後重新計算。';}},getTargets:()=>plan?plan.rows.map(r=>({code:r.code,name:r.name,quantity:r.target})):null};
  };
})(typeof globalThis!=='undefined'?globalThis:this);
