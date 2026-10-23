
(function(){
 const byId=id=>document.getElementById(id),key='v5_ui_seen_signal';
 const current={date:DATA.rebal_date,version:DATA.release_version,hash:DATA.canonical_hash};
 byId('preview-version').textContent=current.version;byId('preview-rebalance').textContent=current.date;byId('preview-basis').textContent=DATA.sig_date;
 let old=null;try{old=JSON.parse(localStorage.getItem(key)||'null');}catch(_){}
 function show(title,text){byId('preview-update-title').textContent=title;byId('preview-update-text').textContent=text;byId('preview-update-notice').hidden=false;}
 function remember(){try{localStorage.setItem(key,JSON.stringify(current));}catch(_){}}
 if(old&&old.date===current.date&&old.hash!==current.hash){show('這週的清單已更新', '上次 '+old.version+' → 目前 '+current.version+'。請重新計算並核對買賣股數；已下單者請先對照差額。');}else{remember();}
 byId('preview-update-dismiss').addEventListener('click',()=>{byId('preview-update-notice').hidden=true;remember();});
 window.renderRevisionStatus=function(status){if(status?.rebalance_date===current.date&&status?.canonical_hash&&status.canonical_hash!==current.hash)show('有較新的本週清單','目前畫面尚未更新。請重新整理並重新計算，再核對下單股數。');};
})();
