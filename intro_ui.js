/* Opening animation: a crowd of cursors works at the same time, each sketching boxes, text lines and buttons
   over the real page sections, then the real page fades in.
   Plays once per day on desktop and can be skipped. */
(function(){
 'use strict';
 const key='v5_intro_seen',today=new Date().toISOString().slice(0,10);
 const forced=/[?&]intro=1\b/.test(location.search);
 let seen=null;try{seen=localStorage.getItem(key);}catch(_){}
 // 只在電腦版播放：手機、平板或窄視窗直接略過。
 const desktop=innerWidth>=1024&&!(window.matchMedia&&matchMedia('(pointer: coarse)').matches);
 if(!forced&&(seen===today||!desktop||!document.body||!Element.prototype.animate))return;
 try{localStorage.setItem(key,today);}catch(_){}

 const css=`
 #v5-intro{position:fixed;inset:0;z-index:2147483000;background:#0b0f19;overflow:hidden;cursor:pointer;font-family:"Segoe UI","Microsoft JhengHei",sans-serif}
 #v5-intro .grid{position:absolute;inset:0;background-image:radial-gradient(rgba(77,163,255,.18) 1px,transparent 1px);background-size:22px 22px;opacity:0}
 #v5-intro .core{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);text-align:center;color:#e8edf5;z-index:3;padding:14px 22px;border-radius:16px;background:rgba(11,15,25,.72)}
 #v5-intro .logo{width:64px;height:64px;margin:0 auto 12px;border-radius:16px;display:grid;place-items:center;font-weight:800;font-size:22px;letter-spacing:1px;
  background:linear-gradient(135deg,#ff4d4f,#f5c542);color:#0b0f19}
 #v5-intro .say{font-size:13px;color:#8fa0b8;letter-spacing:1px;min-height:18px}
 #v5-intro .box{position:absolute;border:1.5px solid var(--c);border-radius:8px;overflow:hidden;width:0;height:0;opacity:0;box-shadow:0 0 12px -2px var(--c)}
 #v5-intro .ln{position:absolute;border-radius:4px;background:var(--c);width:0;opacity:0}
 #v5-intro .ring{position:absolute;width:22px;height:22px;margin:-11px 0 0 -11px;border:2px solid var(--c);border-radius:50%;opacity:0}
 #v5-intro .cur{position:absolute;left:0;top:0;opacity:0;width:18px;height:22px;z-index:2;filter:drop-shadow(0 0 6px var(--c))}
 #v5-intro .cur b{position:absolute;left:14px;top:16px;font:600 10px/1 "Segoe UI","Microsoft JhengHei",sans-serif;color:#0b0f19;background:var(--c);border-radius:4px;padding:2px 4px;white-space:nowrap}
 #v5-intro .skip{position:absolute;right:16px;bottom:16px;z-index:4;background:rgba(255,255,255,.08);color:#e8edf5;border:1px solid rgba(255,255,255,.2);border-radius:999px;padding:6px 14px;font-size:12px;cursor:pointer}
 #v5-intro .bar{position:absolute;left:0;bottom:0;height:3px;width:100%;background:linear-gradient(90deg,#ff4d4f,#f5c542,#4da3ff);transform-origin:left;transform:scaleX(0)}`;
 const style=document.createElement('style');style.textContent=css;document.head.appendChild(style);
 const root=document.createElement('div');root.id='v5-intro';root.setAttribute('aria-hidden','true');
 root.innerHTML='<div class="grid"></div><div class="core"><div class="logo">V5</div><div class="say">召集選股小幫手…</div></div><button class="skip" type="button">跳過 ›</button><div class="bar"></div>';
 document.body.appendChild(root);
 const say=root.querySelector('.say'),core=root.querySelector('.core'),anims=[];
 const play=(el,frames,opts)=>{const a=el.animate(frames,{fill:'forwards',easing:'cubic-bezier(.22,.8,.3,1)',...opts});anims.push(a);return a;};
 let done=false;
 function finish(){
  if(done)return;done=true;
  anims.forEach(a=>{try{a.pause();}catch(_){}});
  root.animate([{opacity:1},{opacity:0}],{duration:450,fill:'forwards'}).onfinish=()=>{root.remove();style.remove();};
 }
 root.addEventListener('click',finish);

 const sel=['header','.version-panel','#freshnessbox','.result-overview','.capital-panel','.orders-panel','#order-planner','#model-reference'];
 const colors=['#4da3ff','#f5c542','#3ddc84','#ff4d4f','#b38cff','#ff8a3d','#2fd4c8','#ff6fb5'];
 const names=['選股','回測','風控','資金','清單','核對','模型','標題','版本','狀態','摘要','排版','配色','數據','圖表','通知','除權','價格','成交','均線','股本','營收','動能','權重'];
 const rnd=(a,b)=>a+Math.random()*(b-a);
 function sections(){
  const H=innerHeight,W=innerWidth,out=[];
  sel.forEach(s=>{const el=document.querySelector(s);if(!el)return;const r=el.getBoundingClientRect();
   if(r.width>20&&r.height>10&&r.top<H-20)out.push({x:r.left,y:r.top,w:r.width,h:Math.min(r.height,H-r.top-8)});});
  if(out.length>=3)return out;
  const m=Math.min(1180,W-32),x=(W-m)/2;
  return [[0,0.05,1,.08],[0,.16,1,.09],[0,.29,.48,.2],[.52,.29,.48,.2],[0,.53,1,.18],[0,.75,1,.18]].map(([a,b,c,d])=>({x:x+a*m,y:b*H,w:c*m,h:d*H}));
 }
 // 每個區塊切成許多小格子，每格由一個滑鼠從上下左右不同方向拉出來。
 function jobs(S){
  const out=[];
  S.forEach(s=>{
   const cols=Math.max(1,Math.round(s.w/150)),rows=Math.max(1,Math.round(s.h/56)),g=6;
   const cw=(s.w-g*(cols-1))/cols,ch=(s.h-g*(rows-1))/rows;
   for(let r=0;r<rows;r++)for(let k=0;k<cols;k++){
    // 偶爾把相鄰兩格合併成寬格，畫面比較不呆板。
    let w=cw;if(k<cols-1&&Math.random()<.25){w=cw*2+g;}
    out.push({x:s.x+k*(cw+g),y:s.y+r*(ch+g),w,h:ch,dir:Math.floor(Math.random()*4)});
    if(w>cw)k++;
   }
  });
  for(let i=out.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[out[i],out[j]]=[out[j],out[i]];}
  return out;
 }
 function start(){
  if(done)return;
  const S=sections(),J=jobs(S),W=innerWidth,H=innerHeight,D=4300;
  const N=Math.min(names.length,Math.max(14,Math.ceil(J.length/4.5)));
  play(root.querySelector('.grid'),[{opacity:0},{opacity:1}],{duration:500});
  play(root.querySelector('.logo'),[{transform:'scale(.6)',opacity:0},{transform:'scale(1.08)',opacity:1,offset:.6},{transform:'scale(1)',opacity:1}],{duration:600});
  play(root.querySelector('.bar'),[{transform:'scaleX(0)'},{transform:'scaleX(1)'}],{duration:D,easing:'linear'});
  setTimeout(()=>{if(!done)say.textContent=N+' 位小幫手同時施工中…';},700);
  setTimeout(()=>{if(!done)say.textContent='完成 ✓';},3500);
  play(core,[{opacity:1},{opacity:1,offset:.78},{opacity:0}],{duration:4200});

  // 所有滑鼠幾乎同時從四周飛進來。
  const cur=[];
  for(let i=0;i<N;i++){
   const side=i%4,sx=side===0?-30:side===1?W+30:rnd(.05,.95)*W,sy=side<2?rnd(.05,.95)*H:(side===2?-30:H+30);
   cur.push({i,c:colors[i%colors.length],x:sx,y:sy,t:rnd(80,450),frames:[{t:0,x:sx,y:sy,o:0}]});
  }
  J.forEach(j=>{
   // 拖曳方向：0 左上→右下、1 右上→左下、2 左下→右上、3 右下→左上。
   const fx=j.dir===1||j.dir===3?j.x+j.w:j.x,fy=j.dir>=2?j.y+j.h:j.y,tx=fx===j.x?j.x+j.w:j.x,ty=fy===j.y?j.y+j.h:j.y;
   let best=null,bs=1e9;
   cur.forEach(c=>{const s=c.t+Math.hypot(fx-c.x,fy-c.y)/3+rnd(90,160);if(s<bs){bs=s;best=c;}});
   const draw=rnd(240,380),end=bs+draw;
   if(end>3500)return;
   const c=best;
   if(c.frames.length===1)c.frames.push({t:Math.max(0,c.t-120),x:c.x,y:c.y,o:0});
   c.frames.push({t:c.t,x:c.x,y:c.y,o:1});
   c.frames.push({t:bs,x:fx,y:fy,o:1});
   c.frames.push({t:end,x:tx,y:ty,o:1});
   c.x=tx;c.y=ty;c.t=end+rnd(40,100);
   const el=document.createElement('div');el.className='box';el.style.setProperty('--c',c.c);
   el.innerHTML='<i class="ln" style="left:10px;top:10px;height:6px"></i>';
   root.insertBefore(el,core);
   const f0={left:fx+'px',top:fy+'px',width:'0px',height:'0px',opacity:1},f1={left:j.x+'px',top:j.y+'px',width:j.w+'px',height:j.h+'px',opacity:1};
   play(el,[f0,f1],{duration:draw,delay:bs,easing:'cubic-bezier(.5,0,.3,1)'});
   play(el,[{background:'transparent'},{background:'rgba(26,34,51,.85)'}],{duration:300,delay:end});
   const ln=el.querySelector('i');ln.style.setProperty('--c',c.c);
   play(ln,[{width:'0px',opacity:.6},{width:Math.max(8,(j.w-20)*rnd(.35,.8))+'px',opacity:.6}],{duration:260,delay:end+40});
   const ring=document.createElement('div');ring.className='ring';ring.style.setProperty('--c',c.c);ring.style.left=fx+'px';ring.style.top=fy+'px';
   root.insertBefore(ring,core);
   play(ring,[{transform:'scale(.3)',opacity:.9},{transform:'scale(1.6)',opacity:0}],{duration:380,delay:bs});
  });
  cur.forEach(c=>{
   if(c.frames.length<2)return;
   // 工作完成後在原地晃一下，最後一起淡出。
   const last=c.frames[c.frames.length-1];
   if(last.t<3500)c.frames.push({t:Math.min(3600,last.t+300),x:last.x+rnd(-30,30),y:last.y+rnd(-20,20),o:1});
   const fin=c.frames[c.frames.length-1];
   c.frames.push({t:3750,x:fin.x,y:fin.y,o:1},{t:4050,x:fin.x,y:fin.y,o:0});
   const el=document.createElement('div');el.className='cur';el.style.setProperty('--c',c.c);
   el.innerHTML='<svg viewBox="0 0 18 22" width="18" height="22"><path d="M1 1 L1 17 L5.5 13 L8.5 20.5 L11.5 19.2 L8.6 12 L15 12 Z" fill="'+c.c+'" stroke="#0b0f19" stroke-width="1.2" stroke-linejoin="round"/></svg><b>'+names[c.i]+'</b>';
   root.appendChild(el);
   let prev=-1;
   const kf=c.frames.filter(f=>{if(f.t<=prev)return false;prev=f.t;return true;}).map(f=>({offset:f.t/4050,transform:`translate(${f.x}px,${f.y}px)`,opacity:f.o,easing:'cubic-bezier(.4,0,.3,1)'}));
   kf[0].offset=0;kf[kf.length-1].offset=1;
   play(el,kf,{duration:4050,easing:'linear'});
  });
  setTimeout(finish,4550);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>requestAnimationFrame(start));else requestAnimationFrame(start);
 setTimeout(finish,7000);
})();
