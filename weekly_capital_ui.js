(function(root){
  'use strict';
  root.initWeeklyCapital=function({data,money,isReady=()=>true,onChange=()=>{}}){
    const $=id=>document.getElementById(id),key='v4_weekly_capital_snapshots_v1';
    let plan=null,saved={};
    try{saved=JSON.parse(localStorage.getItem(key)||'{}');if(!saved||Array.isArray(saved)||typeof saved!=='object')saved={};}catch(_){saved={};}
    function invalidate(){plan=null;$('weekly-capital-copy-note').textContent='';$('weekly-capital-results').hidden=true;$('weekly-capital-error').textContent='請確認兩週總資金後，按「計算本週買賣」。';onChange();}
    function calculate(){
      plan=null;$('weekly-capital-results').hidden=true;$('weekly-capital-copy-note').textContent='';
      try{
        if(!data.sig_validated||!isReady())throw Error('本週訊號尚未確認完成，暫不產生買賣清單；請先確認頁首狀態。');
        const previous=data.previous_capital_signal,current=data.capital_signal;
        const previousCapital=Number($('cap-previous').value),capital=Number($('cap').value);
        const entry=saved[previous?.rebalance_date];
        const snapshot=entry?.capital===previousCapital?entry:null;
        const result=root.weeklyCapitalPlan({previous,current,previousCapital,capital,quotes:data.order_quotes||{},snapshot});
        if(!result.withinBudget)throw Error('受限持股或費用使配置超出本週資金／95%上限，已停止產生可執行清單。');
        plan=result;
        const table=$('weekly-capital-table');table.replaceChildren();
        const head=table.createTHead().insertRow();for(const text of ['股票','本週操作','買賣股數','原有 → 調整後','原因']){const th=document.createElement('th');th.textContent=text;head.appendChild(th);}
        const body=table.createTBody(),labels={BUY:'買進',SELL:'賣出',HOLD:'不用動'};
        const rows=[...plan.rows].filter(r=>r.current||r.target).sort((a,b)=>({SELL:0,BUY:1,HOLD:2}[a.side]-{SELL:0,BUY:1,HOLD:2}[b.side])||a.code.localeCompare(b.code));
        for(const r of rows){const tr=body.insertRow();for(const text of [r.code+' '+r.name,labels[r.side],r.side==='HOLD'?'—':Math.abs(r.change)+' 股',r.current+' → '+r.target+' 股',r.reason])tr.insertCell().textContent=text;}
        const buy=rows.filter(r=>r.side==='BUY').length,sell=rows.filter(r=>r.side==='SELL').length,hold=rows.filter(r=>r.side==='HOLD').length;
        $('weekly-capital-summary').textContent='買進／加碼 '+buy+' 檔｜賣出／減碼 '+sell+' 檔｜不用動 '+hold+' 檔｜調整後股票 '+money(plan.invested)+'（'+(plan.exposure*100).toFixed(2)+'%）｜預估剩餘 '+money(plan.cash)+'｜本次交易費稅 '+money(plan.fees)+'。'+(!plan.bandReached?' 因整股或交易限制，尚未達90%，差額保留現金。':'');
        $('weekly-capital-basis').textContent='上週基準 '+plan.previousDate+' → 本週 '+plan.date+'；'+(plan.source==='saved'?'沿用本瀏覽器已儲存的上週試算股數。':'首次或上週資金不同：依上週模型配置、上週股價及輸入資金推算股數。')+' 本週依各檔頁面基準行情估算，實際成交價會改變費用及剩餘資金；這是理論持股，未連接券商庫存。';
        $('weekly-capital-error').textContent='';$('weekly-capital-results').hidden=false;
        saved[plan.date]=plan.snapshot;
        saved=Object.fromEntries(Object.entries(saved).sort(([a],[b])=>a.localeCompare(b)).slice(-6));
        try{localStorage.setItem(key,JSON.stringify(saved));}catch(_){$('weekly-capital-error').textContent='本次計算有效，但瀏覽器無法儲存股數；下次將依模型推算。';}
        onChange();
      }catch(error){plan=null;$('weekly-capital-error').textContent=error.message;onChange();}
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
