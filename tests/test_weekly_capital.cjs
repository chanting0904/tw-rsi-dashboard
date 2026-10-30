const test=require('node:test'),assert=require('node:assert/strict');
globalThis.previewAllocation=require('../capital_preview.js').previewAllocation;
const {weeklyCapitalPlan}=require('../weekly_capital.js');
function action(code,shares,weight=.474,action='HOLD',price=100){return {stock_code:code,stock_name:code,channel:'A',rank:1,action,allocation_eligible:action!=='SELL',post_shares:shares,current_shares:shares,target_weight:weight,post_weight:weight,strategy_target_weight:weight,price};}
function fixture(){const actions=[action('1101',474),action('1102',474)];return {previous:{rebalance_date:'2026-10-08',actions},current:{rebalance_date:'2026-10-16',actions:structuredClone(actions)},previousCapital:100000,capital:100000,quotes:{1101:{price:100,tradable:true},1102:{price:100,tradable:true}}};}
function check(p){assert.ok(p.withinBudget);assert.ok(p.exposure<=.9500000001);assert.ok(p.cash>=-1e-7);assert.ok(Math.abs(p.invested+p.fees+p.cash-p.capital)<1e-6);assert.ok(p.rows.every(r=>Number.isSafeInteger(r.target)&&r.target>=0));assert.ok(p.rows.filter(r=>!r.tradable).every(r=>r.current===r.target));}
test('same capital does not rebalance every retained holding',()=>{const p=weeklyCapitalPlan(fixture());check(p);assert.ok(p.rows.every(r=>r.side==='HOLD'));assert.equal(p.fees,0);});
test('small capital increase preserves held shares while exposure remains in range',()=>{const f=fixture();f.capital=105000;const p=weeklyCapitalPlan(f);check(p);assert.ok(p.rows.every(r=>r.side==='HOLD'));assert.ok(p.bandReached);});
test('large deposit only tops up when portfolio falls below 90 percent',()=>{const f=fixture();f.capital=150000;const p=weeklyCapitalPlan(f);check(p);assert.ok(p.bandReached);assert.ok(p.rows.some(r=>r.reason.includes('低於90%')));});
test('capital withdrawal sells enough and does not require separate cash input',()=>{const f=fixture();f.capital=60000;const p=weeklyCapitalPlan(f);check(p);assert.ok(p.rows.some(r=>r.side==='SELL'));});
test('exit and new entry give exact share differences, retained stock stays unchanged',()=>{const f=fixture();f.current.actions[1]=action('1102',0,0,'SELL');f.current.actions[1].current_shares=474;f.current.actions.push(action('1103',474));f.current.actions[2].current_shares=0;f.quotes[1103]={price:100,tradable:true};const p=weeklyCapitalPlan(f);check(p);assert.equal(p.rows.find(r=>r.code==='1101').change,0);assert.equal(p.rows.find(r=>r.code==='1102').change,-474);assert.ok(p.rows.find(r=>r.code==='1103').change>0);});
test('saved theoretical shares take precedence over reconstructed model shares',()=>{const f=fixture();f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:470},{code:'1102',shares:460}]};const p=weeklyCapitalPlan(f);check(p);assert.equal(p.source,'saved');assert.equal(p.rows.find(r=>r.code==='1101').target,470);assert.equal(p.rows.find(r=>r.code==='1102').target,460);});
test('verified share delivery ratio changes available original shares without a fake buy',()=>{const f=fixture();f.current.actions[0].current_shares=948;f.quotes[1101].price=50;const p=weeklyCapitalPlan(f);check(p);assert.equal(p.rows.find(r=>r.code==='1101').current,948);assert.ok(p.rows.every(r=>r.side==='HOLD'));});
test('fees charged only on differences with sale tax, not on unchanged positions',()=>{const f=fixture();f.capital=60000;const p=weeklyCapitalPlan(f);check(p);const expected=p.rows.reduce((s,r)=>s+(r.change?Math.max(1,Math.abs(r.change)*r.price*.000399)+(r.change<0?Math.abs(r.change)*r.price*.003:0):0),0);assert.ok(Math.abs(p.fees-expected)<1e-8);});
test('blocked exit stays held and cannot silently sell',()=>{const f=fixture();f.current.actions[0].action='SELL';f.current.actions[0].allocation_eligible=false;f.quotes[1101].tradable=false;const p=weeklyCapitalPlan(f);check(p);assert.equal(p.rows.find(r=>r.code==='1101').change,0);});
test('blocked portfolio that exceeds capital is not approved as executable',()=>{const f=fixture();f.capital=10000;f.quotes[1101].tradable=false;f.quotes[1102].tradable=false;const p=weeklyCapitalPlan(f);assert.equal(p.withinBudget,false);});
test('missing or broken inputs fail closed',()=>{
 for(const amount of [0,NaN,Infinity,1e20]){const f=fixture();f.previousCapital=amount;assert.throws(()=>weeklyCapitalPlan(f));}
 let f=fixture();f.quotes[1101].price=null;assert.throws(()=>weeklyCapitalPlan(f),/股價/);
 f=fixture();f.current.actions.pop();assert.throws(()=>weeklyCapitalPlan(f),/處置資料/);
 f=fixture();f.current.rebalance_date=f.previous.rebalance_date;assert.throws(()=>weeklyCapitalPlan(f),/上週/);
 f=fixture();f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:150000,holdings:[]};assert.throws(()=>weeklyCapitalPlan(f),/不符/);
});
test('rounding shortage stays cash rather than breaking 95 percent cap',()=>{const f=fixture();f.previousCapital=10000;f.capital=10000;f.previous.actions=[action('1101',1,.95,'HOLD',9000)];f.current.actions=structuredClone(f.previous.actions);f.quotes={1101:{price:9000,tradable:true}};const p=weeklyCapitalPlan(f);check(p);assert.ok(p.exposure<=.95);});
test('deterministic random capital and price cases conserve total assets',()=>{
 let seed=1718;const rand=()=>{seed=(1664525*seed+1013904223)>>>0;return seed/4294967296;};
 for(let i=0;i<1500;i++){const f=fixture();f.previousCapital=Math.round(10000+rand()*990000);f.capital=Math.round(10000+rand()*1990000);for(const q of Object.values(f.quotes))q.price=Math.round((10+rand()*190)*100)/100;check(weeklyCapitalPlan(f));}
});

test('one share can be fully sold when its value exceeds twice the current target',()=>{
 const f=fixture();f.previous.actions=[action('1101',1,.0003,'HOLD',30),action('1102',947,.947)];
 f.current.actions=structuredClone(f.previous.actions);f.current.actions[0].target_weight=.00061;
 f.quotes[1101].price=61;f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:1},{code:'1102',shares:947}]};
 const p=weeklyCapitalPlan(f);check(p);assert.equal(p.rows.find(r=>r.code==='1101').change,-1);
});
test('exactly double the target does not cross the general 100 percent threshold',()=>{
 const f=fixture();f.previous.actions=[action('1101',1,.0003,'HOLD',30),action('1102',947,.947)];f.current.actions=structuredClone(f.previous.actions);
 f.quotes[1101].price=60;f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:1},{code:'1102',shares:947}]};
 const p=weeklyCapitalPlan(f);check(p);assert.equal(p.rows.find(r=>r.code==='1101').change,0);
});
test('confirmed zero write-off does not create a sale, cash or a missing-action failure',()=>{
 for(const present of [false,true]){const f=fixture();f.capital=52600;f.current.restricted_assets=[{code:'1102',book_value:0,policy:'zero',shares:474}];
  f.current.actions[0].target_weight=.90;f.current.actions[0].strategy_target_weight=.90;
  if(present){f.current.actions[1].allocation_eligible=false;f.quotes[1102].tradable=false;}else f.current.actions.pop();
  const p=weeklyCapitalPlan(f);check(p);assert.equal(p.rows.length,1);assert.equal(p.rows[0].change,0);assert.equal(p.invested,47400);assert.equal(p.cash,5200);assert.equal(p.fees,0);assert.ok(!p.snapshot.holdings.some(r=>r.code==='1102'));
 }
});
test('real tradable holdings are not written off by a historical zero record',()=>{
 const f=fixture();f.current.restricted_assets=[{code:'1102',book_value:0,policy:'zero'}];const p=weeklyCapitalPlan(f);check(p);assert.ok(p.rows.some(r=>r.code==='1102'));
});
test('valid saved shares do not require an unused prior-week price reconstruction',()=>{
 const f=fixture();f.previous.actions[0].price=null;f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:474},{code:'1102',shares:474}]};const p=weeklyCapitalPlan(f);check(p);assert.equal(p.source,'saved');assert.ok(p.rows.every(r=>r.change===0));
});
test('verified delivery after a restricted prior position is a share adjustment, not a buy',()=>{
 const f=fixture();f.previous.actions[0].allocation_eligible=false;f.current.actions[0].current_shares=948;f.quotes[1101].price=50;
 f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:474},{code:'1102',shares:474}]};const p=weeklyCapitalPlan(f);check(p);assert.equal(p.rows.find(r=>r.code==='1101').current,948);assert.ok(p.rows.every(r=>r.change===0));
});
test('mark to market gains change total capital once, leaving unchanged cash and no fake trades',()=>{
 const f=fixture();f.previous.actions=[action('1101',930,.93)];f.current.actions=structuredClone(f.previous.actions);f.quotes={1101:{price:101,tradable:true}};f.capital=100930;
 f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:930}]};const p=weeklyCapitalPlan(f);check(p);assert.equal(p.invested,93930);assert.equal(p.cash,7000);assert.equal(p.fees,0);assert.equal(p.rows[0].change,0);
});

test('invalid tiny quote with existing shares fails quickly before quantity search',()=>{
 const {spawnSync}=require('node:child_process');const modulePath=require.resolve('../weekly_capital.js');
 const code='const {weeklyCapitalPlan}=require('+JSON.stringify(modulePath)+');const a={stock_code:"1101",post_shares:1,current_shares:1,allocation_eligible:true,action:"HOLD",target_weight:.95,strategy_target_weight:.95};try{weeklyCapitalPlan({previous:{rebalance_date:"2026-10-08",actions:[a]},current:{rebalance_date:"2026-10-16",actions:[a]},previousCapital:100000,capital:1000000000000,quotes:{1101:{price:1e-10,tradable:true}},snapshot:{schema:1,rebalance_date:"2026-10-08",capital:100000,holdings:[{code:"1101",shares:1}]}});process.exit(2);}catch(e){if(!e.message.includes("安全整數"))throw e;}';
 const r=spawnSync(process.execPath,['-e',code],{timeout:3000,encoding:'utf8'});assert.equal(r.error,undefined);assert.equal(r.status,0,r.stderr);
});

test('inflated model carry weight cannot cause a sold one-share position to be bought back next week',()=>{
 const f=fixture();f.previous.actions=[action('1101',1,.0003,'HOLD',30),action('1102',947,.947)];f.current.actions=structuredClone(f.previous.actions);f.current.actions[0].target_weight=.00061;f.current.actions[0].post_weight=.00061;f.quotes[1101].price=61;f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:1},{code:'1102',shares:947}]};
 const p=weeklyCapitalPlan(f);check(p);assert.equal(p.rows.find(r=>r.code==='1101').change,-1);
 const next=weeklyCapitalPlan({...f,previous:f.current,current:{...f.current,rebalance_date:'2026-10-23'},capital:p.nav,snapshot:p.snapshot});check(next);assert.equal(next.rows.find(r=>r.code==='1101').target,0);assert.equal(next.fees,0);
});

const {spawnSync}=require('node:child_process');
test('server bridge and browser use the same guarded quantities',()=>{
 const previous={rebalance_date:'2026-10-08',actions:[{stock_code:'1101',post_shares:100}]};
 const current={rebalance_date:'2026-10-16',actions:[{stock_code:'1101',stock_name:'虛構',channel:'A',action:'HOLD',allocation_eligible:true,current_shares:100,target_weight:.9,planner_target_weight:.8,strategy_target_weight:.15}]};
 const args={previous,current,previousCapital:100000,capital:100000,quotes:{1101:{price:100,tradable:true}},snapshot:{schema:1,rebalance_date:previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:100}]}};
 const local=weeklyCapitalPlan(args);
 const r=spawnSync(process.execPath,[require('node:path').resolve(__dirname,'../v5_engine/guarded_bridge.cjs')],{input:JSON.stringify(args)+'\n',encoding:'utf8'});
 assert.equal(r.status,0,r.stderr);const remote=JSON.parse(r.stdout);assert.equal(remote.ok,true);assert.deepEqual(remote.plan,JSON.parse(JSON.stringify(local)));
 assert.ok(local.rows[0].target<=150);
});
test('locked receivables cannot fund purchases',()=>{
 const previous={rebalance_date:'2026-10-08',actions:[]},current={rebalance_date:'2026-10-16',actions:[{stock_code:'1101',stock_name:'虛構',channel:'A',action:'BUY',allocation_eligible:true,current_shares:0,target_weight:.95,strategy_target_weight:.95}]};
 const p=weeklyCapitalPlan({previous,current,previousCapital:100000,capital:100000,reservedAssets:80000,quotes:{1101:{price:100,tradable:true}}});
 assert.ok(p.cash>=0);assert.ok(p.invested+p.fees<=20000);assert.equal(p.reservedAssets,80000);
});
test('a limit-up blocks buying without blocking a sale',()=>{
 const previous={rebalance_date:'2026-10-08',actions:[{stock_code:'1101',post_shares:1000}]},current={rebalance_date:'2026-10-16',actions:[{stock_code:'1101',stock_name:'虛構',channel:'A',action:'HOLD',allocation_eligible:true,current_shares:1000,target_weight:.9,strategy_target_weight:.2}]};
 const p=weeklyCapitalPlan({previous,current,previousCapital:100000,capital:100000,quotes:{1101:{price:100,tradable:true,buyable:false,sellable:true}},snapshot:{schema:1,rebalance_date:previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:1000}]}});
 assert.equal(p.rows[0].target,200);assert.equal(p.rows[0].side,'SELL');
});
test('a limit-down prevents sale and fails closed when the cap cannot be reached',()=>{
 const previous={rebalance_date:'2026-10-08',actions:[{stock_code:'1101',post_shares:1000}]},current={rebalance_date:'2026-10-16',actions:[{stock_code:'1101',stock_name:'虛構',channel:'A',action:'SELL',allocation_eligible:false,current_shares:1000,target_weight:0,strategy_target_weight:0}]};
 const p=weeklyCapitalPlan({previous,current,previousCapital:100000,capital:100000,quotes:{1101:{price:100,tradable:true,buyable:true,sellable:false}},snapshot:{schema:1,rebalance_date:previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:1000}]}});
 assert.equal(p.rows[0].target,1000);assert.equal(p.withinBudget,false);
});

test('delivered stock rights remain owned after all original shares were sold',()=>{
 const previous={rebalance_date:'2026-10-08',actions:[],pending_assets:[{code:'1101',shares:5,ex_date:'2026-09-01',tradable_date:'2026-10-12'}]};
 const current={rebalance_date:'2026-10-16',actions:[{stock_code:'1101',stock_name:'虛構',channel:'A',action:'SELL',allocation_eligible:false,current_shares:5,target_weight:0,strategy_target_weight:0}],pending_assets:[]};
 const p=weeklyCapitalPlan({previous,current,previousCapital:100000,capital:100000,quotes:{1101:{price:100,tradable:true}},snapshot:{schema:1,rebalance_date:previous.rebalance_date,capital:100000,holdings:[],pendingHoldings:[{code:'1101',shares:5,ex_date:'2026-09-01',tradable_date:'2026-10-12'}]}});
 assert.equal(p.rows[0].current,5);assert.equal(p.rows[0].change,-5);
});
test('retained shares receive delivered rights exactly once',()=>{
 const f=fixture();f.previous.actions[0].post_shares=100;f.current.actions[0].current_shares=105;
 f.previous.pending_assets=[{code:'1101',shares:5,ex_date:'2026-09-01',tradable_date:'2026-10-12'}];f.current.pending_assets=[];
 f.snapshot={schema:1,rebalance_date:f.previous.rebalance_date,capital:100000,holdings:[{code:'1101',shares:100},{code:'1102',shares:474}],pendingHoldings:[{code:'1101',shares:5,ex_date:'2026-09-01',tradable_date:'2026-10-12'}]};
 assert.equal(weeklyCapitalPlan(f).rows.find(r=>r.code==='1101').current,105);
});

test('published weekly list carries to next week share-for-share whatever prices do',()=>{
 const fs=require('fs'),path=require('path'),read=f=>JSON.parse(fs.readFileSync(path.join(__dirname,'../output',f),'utf8'));
 const previous=read('previous_capital_signal.json'),current=read('latest_signals.json');
 const quotes=(s,move=1)=>Object.fromEntries(s.actions.map(a=>[a.stock_code,{price:a.price*move,tradable:true}]));
 const next={...structuredClone(current),rebalance_date:'2099-01-02',actions:current.actions.map(a=>({...a,current_shares:a.post_shares,action:a.post_shares>0?'HOLD':a.action}))};
 for(const capital of [100810,250000,1000000]){
  const week=weeklyCapitalPlan({previous,current,previousCapital:100037,capital,quotes:quotes(current)});
  for(const move of [.5,1,1.8]){
   const carried=weeklyCapitalPlan({previous:current,current:next,previousCapital:capital,capital,quotes:quotes(current,move),snapshot:week.snapshot});
   for(const h of week.snapshot.holdings)assert.equal(carried.rows.find(r=>r.code===h.code).current,h.shares,h.code+' '+capital+' '+move);
  }
 }
});
