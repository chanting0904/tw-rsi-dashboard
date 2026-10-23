
function openMonthlyDetails(key){
 const month=DATA.perf?.monthly_contributions?.months?.[key];if(!month)return;
 const byId=id=>document.getElementById(id),dialog=byId('month-detail-dialog');
 const percent=n=>{const value=Number(n.toFixed(2));return(value>0?'+':'')+value.toFixed(2)+'%';};
 byId('month-detail-title').textContent=key.slice(0,4)+' 年 '+Number(key.slice(5))+' 月';
 byId('month-detail-period').textContent='策略當月 '+percent(month.return_pct)+' · 截至 '+month.through;
 for(const [kind,row] of [['winner',month.winner],['loser',month.loser]]){
  byId('month-'+kind+'-code').textContent=row?row.code:'—';
  byId('month-'+kind+'-name').textContent=row?row.name:kind==='winner'?'本月沒有獲利股票':'本月沒有虧損股票';
  byId('month-'+kind+'-points').textContent=row?(row.contribution>0?'+':'−')+Math.abs(row.contribution).toFixed(2)+' 個百分點':'—';
  byId('month-'+kind+'-contribution').textContent=row?'對本月策略報酬的貢獻':'';
 }
 if(!dialog.open)dialog.showModal();
}
(function(){const dialog=document.getElementById('month-detail-dialog');document.getElementById('month-detail-close').addEventListener('click',()=>dialog.close());dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();});})();
