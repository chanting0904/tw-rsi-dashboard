const test=require('node:test'),assert=require('node:assert/strict');
globalThis.previewAllocation=require('../capital_preview.js').previewAllocation;
const {weeklyCapitalPlan}=require('../weekly_capital.js');
function action(code,shares,weight=.475,action='HOLD',price=100){return {stock_code:code,stock_name:code,channel:'A',rank:1,action,allocation_eligible:action!=='SELL',post_shares:shares,current_shares:shares,target_weight:weight,post_weight:weight,strategy_target_weight:weight,price};}
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
