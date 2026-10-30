const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..');
const read=name=>process.env.CAPITAL_CARRY_BASELINE==='1'&&['weekly_capital.js','weekly_capital_ui.js'].includes(name)
 ? require('node:child_process').execFileSync('git',['show','HEAD:'+name],{cwd:root,encoding:'utf8'})
 : fs.readFileSync(path.join(root,name),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
// Frozen public economic inputs: later scheduled publications must not alter this regression.
function economicFixture(meta,rows){return {...meta,actions:rows.map(r=>({...Object.fromEntries(["stock_code","price","current_shares","post_shares","post_weight","target_weight","strategy_target_weight","planner_target_weight","action","allocation_eligible","trading_status","buy_allowed","sell_allowed"].map((key,i)=>[key,r[i]])),stock_name:r[0],channel:'A',price_date:meta.price_basis_date}))};}
const signal=economicFixture({"rebalance_date":"2026-10-23","price_basis_date":"2026-10-22","buy_fee":0.000399,"minimum_commission":1.0,"capital_basis":8026553.171734453,"receivables":9490.87,"pending_assets":[],"stock_deliveries":[{"code":"2753","ex_date":"2026-08-26","tradable_date":"2026-10-23"},{"code":"2364","ex_date":"2026-08-28","tradable_date":"2026-10-20"},{"code":"7584","ex_date":"2026-08-31","tradable_date":"2026-10-23"},{"code":"9917","ex_date":"2026-08-31","tradable_date":"2026-10-21"},{"code":"3416","ex_date":"2026-09-01","tradable_date":"2026-10-20"},{"code":"6874","ex_date":"2026-09-03","tradable_date":"2026-10-19"}],"restricted_assets":[{"code":"3089","name":"億杰科技股份有限公司","shares":1099,"pool":"A","book_value":0.0,"last_quote_date":"2023-07-19","last_quote":81.0,"policy":"zero"}],"fee_model":{"commission_rate":0.000399,"minimum_commission":1.0,"stock_sale_tax":0.003,"etf_sale_tax":0.001,"order_policy":"one_minimum_per_code_side_rebalance"}},[
 ["2412",158.0,0,4022,0.07936,0.07936,0.07918,0.07918,"BUY",true,"TRADABLE",true,true],
 ["6108",32.75,0,19405,0.07936,0.07936,0.07918,0.07918,"BUY",true,"TRADABLE",true,true],
 ["6277",100.5,0,6323,0.07936,0.07936,0.07918,0.07917,"BUY",true,"TRADABLE",true,true],
 ["2033",67.9,7764,10096,0.08561,0.08561,0.07918,0.08541,"REBALANCE_BUY",true,"TRADABLE",true,true],
 ["5465",41.8,9737,15955,0.08328,0.08328,0.07918,0.08309,"REBALANCE_BUY",true,"TRADABLE",true,true],
 ["6756",125.5,0,3374,0.05288,0.05288,0.05279,0.05277,"BUY",true,"TRADABLE",true,true],
 ["6199",149.5,2842,4178,0.078,0.078,0.07918,0.07782,"REBALANCE_BUY",true,"TRADABLE",true,true],
 ["2466",261.0,1139,1885,0.06144,0.06144,0.05279,0.0613,"REBALANCE_BUY",true,"TRADABLE",true,true],
 ["6214",201.0,0,2106,0.05286,0.05286,0.05279,0.05276,"BUY",true,"TRADABLE",true,true],
 ["1416",13.25,11642,45330,0.07501,0.07501,0.07918,0.07483,"REBALANCE_BUY",true,"TRADABLE",true,true],
 ["6021",53.4,6483,10723,0.07151,0.07151,0.07918,0.07134,"REBALANCE_BUY",true,"TRADABLE",true,true],
 ["6278",290.5,0,728,0.02641,0.02641,0.02639,0.02636,"BUY",true,"TRADABLE",true,true],
 ["6672",581.0,407,565,0.04099,0.04099,0.05279,0.04885,"REBALANCE_BUY",true,"TRADABLE",true,true],
 ["2884",49.75,0,4257,0.02645,0.02645,0.02639,0.02639,"BUY",true,"TRADABLE",true,true],
 ["6213",777.0,335,335,0.03251,0.03251,0.05279,0.0501,"HOLD",true,"TRADABLE",true,true],
 ["1709",55.3,10029,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["6221",36.4,10376,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["3265",226.5,1344,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["1615",47.85,7165,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["2607",55.7,4122,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["3311",44.0,4982,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["6505",106.5,983,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["1303",301.0,2079,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["2409",35.4,21531,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["3008",6175.0,118,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["3189",986.0,732,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
]);
const priorSignal=economicFixture({"rebalance_date":"2026-10-16","price_basis_date":"2026-10-16","buy_fee":0.000399,"minimum_commission":1.0,"capital_basis":8039202.684533853,"receivables":9490.87,"pending_assets":[],"stock_deliveries":[{"code":"6968","ex_date":"2026-08-24","tradable_date":"2026-10-12"},{"code":"1734","ex_date":"2026-08-27","tradable_date":"2026-10-15"},{"code":"7728","ex_date":"2026-08-27","tradable_date":"2026-10-13"},{"code":"8932","ex_date":"2026-08-28","tradable_date":"2026-10-16"},{"code":"5392","ex_date":"2026-08-31","tradable_date":"2026-10-15"},{"code":"6669","ex_date":"2026-09-02","tradable_date":"2026-10-14"},{"code":"8473","ex_date":"2026-09-08","tradable_date":"2026-10-12"},{"code":"1438","ex_date":"2026-09-09","tradable_date":"2026-10-13"},{"code":"1815","ex_date":"2026-09-09","tradable_date":"2026-10-13"},{"code":"5906","ex_date":"2026-09-10","tradable_date":"2026-10-15"}],"restricted_assets":[{"code":"3089","name":"億杰科技股份有限公司","shares":1099,"pool":"A","book_value":0.0,"last_quote_date":"2023-07-19","last_quote":81.0,"policy":"zero"}],"fee_model":{"commission_rate":0.000399,"minimum_commission":1.0,"stock_sale_tax":0.003,"etf_sale_tax":0.001,"order_policy":"one_minimum_per_code_side_rebalance"}},[
 ["6021",55.8,0,6483,0.04504,0.04504,0.04501,0.045,"BUY",true,"TRADABLE",true,true],
 ["3265",269.0,0,1344,0.04502,0.04502,0.04501,0.04498,"BUY",true,"TRADABLE",true,true],
 ["1615",50.5,0,7165,0.04505,0.04505,0.04501,0.04501,"BUY",true,"TRADABLE",true,true],
 ["1416",13.2,0,11642,0.01913,0.01913,0.04501,0.04501,"BUY",true,"TRADABLE",true,true],
 ["6199",144.0,2842,2842,0.05096,0.05096,0.04501,0.04777,"HOLD",true,"TRADABLE",true,true],
 ["2607",58.5,0,4122,0.03002,0.03002,0.03001,0.03,"BUY",true,"TRADABLE",true,true],
 ["2466",211.5,0,1139,0.02999,0.02999,0.03001,0.02999,"BUY",true,"TRADABLE",true,true],
 ["6213",774.0,335,335,0.03228,0.03228,0.03001,0.03114,"HOLD",true,"TRADABLE",true,true],
 ["6672",591.0,0,407,0.02995,0.02995,0.03001,0.02996,"BUY",true,"TRADABLE",true,true],
 ["3311",48.4,0,4982,0.03002,0.03002,0.03001,0.03,"BUY",true,"TRADABLE",true,true],
 ["5465",36.05,9737,9737,0.04371,0.04371,0.04501,0.04663,"HOLD",true,"TRADABLE",true,true],
 ["6505",122.5,0,983,0.01499,0.01499,0.015,0.01498,"BUY",true,"TRADABLE",true,true],
 ["6221",36.05,10376,10376,0.04657,0.04657,0.04501,0.04848,"HOLD",true,"TRADABLE",true,true],
 ["2033",59.8,7764,7764,0.05781,0.05781,0.04501,0.05092,"HOLD",true,"TRADABLE",false,true],
 ["1709",54.9,10029,10029,0.06855,0.06855,0.04501,0.05467,"HOLD",true,"TRADABLE",true,true],
 ["1303",307.0,2079,2079,0.07947,0.07947,0.09502,0.07427,"HOLD",true,"TRADABLE",true,true],
 ["2409",35.9,21531,21531,0.09624,0.09624,0.09502,0.09548,"HOLD",true,"TRADABLE",true,true],
 ["3189",1000.0,732,732,0.09114,0.09114,0.09502,0.09094,"HOLD",true,"TRADABLE",true,true],
 ["3008",6400.0,118,118,0.09403,0.09403,0.09502,0.09477,"HOLD",true,"TRADABLE",true,true],
 ["6168",35.8,7568,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["6122",61.6,5871,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["3219",116.5,1946,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["3016",154.0,1385,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["2034",23.5,10423,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["6743",32.5,11415,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",true,true],
 ["5013",43.75,7646,0,0.0,0.0,0.0,0.0,"SELL",false,"TRADABLE",false,true],
]);
const storageKey='v4_weekly_capital_snapshots_v1';

function nextWeek(previous){
 const current=clone(previous);
 current.rebalance_date='2026-10-30';current.price_basis_date='2026-10-29';
 current.actions=current.actions.map(a=>({...a,current_shares:a.post_shares}));
 return current;
}
function quotesOf(current,overrides={}){
 return Object.fromEntries(current.actions.map(a=>[a.stock_code,{name:a.stock_name,price:overrides[a.stock_code]??a.price,date:current.price_basis_date,tradable:['TRADABLE','OUTSIDE_TARGET'].includes(a.trading_status),buyable:a.buy_allowed!==false,sellable:a.sell_allowed!==false}]));
}

test('a verified missing-quote session keeps published shares with a same-week model reference',()=>{
 const current=nextWeek(signal),model={...clone(signal),rebalance_date:'2026-10-22',published_reference_date:signal.rebalance_date,reference_reason:'NO_OFFICIAL_QUOTES_ON_PUBLISHED_DATE'};
 const f=fixture({...liveData(signal,current),previous_model_signal:model});
 const result=f.calculate();
 assert.equal(result.rows.find(r=>r.code==='2466').current,23);
 assert.equal(result.rows.find(r=>r.code==='2033').current,125);
});

test('a reference from a different week remains blocked despite a claimed publication mapping',()=>{
 const current=nextWeek(signal),model={...clone(signal),rebalance_date:'2026-10-15',published_reference_date:signal.rebalance_date,reference_reason:'NO_OFFICIAL_QUOTES_ON_PUBLISHED_DATE'};
 const f=fixture({...liveData(signal,current),previous_model_signal:model});
 assert.throws(()=>f.calculate(),/配股及拆併股銜接日期不一致/);
});
function fixture(data,stored={}){
 const elements={},make=()=>({value:'',textContent:'',className:'',hidden:false,children:[],listeners:{},
  addEventListener(type,handler){this.listeners[type]=handler;},replaceChildren(){this.children=[];},
  createTHead(){return this;},createTBody(){return this;},insertRow(){return this;},insertCell(){return make();},
  appendChild(child){this.children.push(child);},append(...children){this.children.push(...children);}});
 const $=id=>elements[id]??=(make());let local=JSON.stringify(stored);const calls=[];
 const scope={document:{getElementById:$,createElement:make},localStorage:{getItem:()=>local,setItem:(key,value)=>{assert.equal(key,storageKey);local=value;},removeItem:()=>{local='{}';}},navigator:{clipboard:{writeText:async()=>{}}}};
 for(const name of ['capital_preview.js','weekly_capital.js'])vm.runInNewContext(read(name),scope);
 const real=scope.weeklyCapitalPlan;
 scope.weeklyCapitalPlan=args=>{const result=real(args);calls.push({args,result});return result;};
 vm.runInNewContext(read('weekly_capital_ui.js'),scope);
 const api=scope.initWeeklyCapital({data,money:String,isReady:()=>true});
 function calculate(previousCapital=100037,capital=100037){
  const before=calls.length;
  $('cap-previous').value=String(previousCapital);$('cap').value=String(capital);$('cap-apply').listeners.click();
  assert.equal($('weekly-capital-results').hidden,false,$('weekly-capital-error').textContent);
  assert.ok(api.getTargets(),$('weekly-capital-error').textContent);
  return calls[before].result;
 }
 return {$,calls,api,data,calculate,stored:()=>JSON.parse(local)};
}
function entryOf(stored,date){const e=stored[date];return e&&e.byCapital?e.byCapital[e.byCapital.length-1]:e;}
function ownRow(result,code='2466'){const row=result.rows.find(r=>r.code===code);assert.ok(row,code+' row missing');return row;}
function directPlan(previous,current,previousCapital,capital,quotes,adjustmentReference=null){
 const scope={};for(const name of ['capital_preview.js','weekly_capital.js'])vm.runInNewContext(read(name),scope);
 return scope.weeklyCapitalPlan({previous,current,previousCapital,capital,quotes,adjustmentReference});
}
function liveData(previous=priorSignal,current=signal,overrides={}){
 return {sig_validated:true,previous_capital_signal:clone(previous),capital_signal:clone(current),order_quotes:quotesOf(current,overrides)};
}

test('next-week reconstruction carries23 shares from the actual dated2466 position instead of refilling its old portfolio',()=>{
 assert.equal(signal.rebalance_date,'2026-10-23');
 const current=nextWeek(signal);
 for(const capital of [100037,101000,105000])for(const price of [261,315.5,350]){
  const result=directPlan(signal,current,100037,capital,quotesOf(current,{'2466':price}));
  assert.equal(ownRow(result).current,23,`capital=${capital}, quote=${price}`);
 }
});

test('legacy stored19-share preview is not reused as a confirmed previous holding',()=>{
 const previous=clone(signal),current=nextWeek(signal);
 const legacy={schema:1,rebalance_date:previous.rebalance_date,capital:100037,holdings:[{code:'2466',shares:19}],pendingHoldings:[]};
 const f=fixture(liveData(previous,current,{'2466':315.5}),{[previous.rebalance_date]:legacy});
 const result=f.calculate(100037,101000);
 assert.equal(ownRow(result).current,23);
 assert.equal(f.calls[0].args.snapshot,null);
});

test('daily quote changes cannot change the shown list or overwrite the23-share dated carry anchor',()=>{
 const f=fixture(liveData());
 const first=f.calculate();
 const saved0=entryOf(f.stored(),signal.rebalance_date);
 assert.equal(saved0.price_policy,'signal_close_v2');
 assert.ok(saved0.signal_basis);
 assert.equal(saved0.holdings.find(h=>h.code==='2466').shares,23);
 assert.equal(ownRow(first).target,23);
 f.data.order_quotes['2466'].price=315.5;
 f.data.order_quotes['2466'].date='2026-10-28';
 f.calculate();
 assert.equal(f.api.getTargets().find(t=>t.code==='2466').quantity,23,'shown list must equal the carried list');
 assert.equal(entryOf(f.stored(),signal.rebalance_date).holdings.find(h=>h.code==='2466').shares,23);
 assert.deepEqual(entryOf(f.stored(),signal.rebalance_date).holdings,saved0.holdings);
});

test('a real prior trim snapshot survives into the next week and is not replaced by model reconstruction',()=>{
 const previous={rebalance_date:'2026-10-16',price_basis_date:'2026-10-15',buy_fee:.000399,minimum_commission:1,capital_basis:100000,actions:[{stock_code:'1101',stock_name:'fixture',channel:'A',price:100,post_shares:950,post_weight:.95,target_weight:.95,allocation_eligible:true,action:'HOLD',trading_status:'TRADABLE'}]};
 const current={rebalance_date:'2026-10-23',price_basis_date:'2026-10-22',buy_fee:.000399,minimum_commission:1,capital_basis:300000,actions:[{...previous.actions[0],price:300,current_shares:950,strategy_target_weight:.95,planner_target_weight:.95}]};
 const first=fixture(liveData(previous,current));const trimmed=first.calculate(100000,100000);
 const held=ownRow(trimmed,'1101').target;
 assert.ok(held<Math.floor(100000*.95/300),'fixture must create a fee-aware actual trim');
 const stored=first.stored();assert.equal(entryOf(stored,current.rebalance_date).price_policy,'signal_close_v2');
 const future={...clone(current),rebalance_date:'2026-10-30',price_basis_date:'2026-10-29'};
 const second=fixture(liveData(current,future),stored);const result=second.calculate(100000,100000);
 assert.equal(ownRow(result,'1101').current,held);
 assert.equal(second.calls[0].args.snapshot.holdings.find(h=>h.code==='1101').shares,held);
});

test('matching economic signal still reuses its stable snapshot after a display release bump',()=>{
 const first=fixture(liveData());first.calculate();const stored=first.stored();
 const previous=clone(signal);previous.release_version='V5.99.99';
 const second=fixture(liveData(previous,nextWeek(previous)),stored);second.calculate();
 assert.equal(second.calls[0].args.snapshot.price_policy,'signal_close_v2');
 assert.equal(ownRow(second.calls[0].result).current,23);
});

test('a stable marker with the wrong economic basis is ignored rather than carrying19 shares',()=>{
 const first=fixture(liveData());first.calculate();const stored=first.stored();
 const entry=entryOf(stored,signal.rebalance_date);entry.price_policy='signal_close_v2';entry.signal_basis='unrelated signal';
 entry.holdings.find(h=>h.code==='2466').shares=19;
 const second=fixture(liveData(signal,nextWeek(signal)),stored);const result=second.calculate();
 assert.equal(second.calls[0].args.snapshot,null);
 assert.equal(ownRow(result).current,23);
});

test('adding stable snapshot metadata preserves pending stock-dividend shares',()=>{
 const current=clone(signal);
 current.pending_assets=[{code:'2466',shares:188,ex_date:'2026-10-20',tradable_date:null,grant_ratio:1.1}];
 const f=fixture(liveData(priorSignal,current));f.calculate();
 const entry=entryOf(f.stored(),current.rebalance_date);
 assert.equal(entry.price_policy,'signal_close_v2');
 assert.deepEqual(JSON.parse(JSON.stringify(entry.pendingHoldings)),[{code:'2466',shares:1,ex_date:'2026-10-20',tradable_date:null}]);
});


test('recomputed model holdings cannot masquerade as a split in the published holding',()=>{
 const reference=clone(signal),current=nextWeek(signal);
 reference.actions.find(a=>a.stock_code==='2466').post_shares=2000;
 current.actions.find(a=>a.stock_code==='2466').current_shares=2000;
 const result=directPlan(signal,current,100037,101000,quotesOf(current,{'2466':315.5}),reference);
 assert.equal(ownRow(result).current,23);
 current.actions.find(a=>a.stock_code==='2466').current_shares=4000;
 const split=directPlan(signal,current,100037,101000,quotesOf(current,{'2466':130.5}),reference);
 assert.equal(ownRow(split).current,46);
});

test('missing previous price stops reconstruction instead of borrowing a newer quote',()=>{
 const previous=clone(signal);previous.actions.find(a=>a.stock_code==='2466').price=null;
 assert.throws(()=>directPlan(previous,nextWeek(signal),100037,101000,quotesOf(signal,{'2466':315.5})),/缺少股價/);
});


test('2033 carries the125-share published-capital position instead of using fee-reduced NAV weights',()=>{
 const scope={};for(const name of ['capital_preview.js','weekly_capital.js'])vm.runInNewContext(read(name),scope);
 const previous=clone(signal),current=nextWeek(signal);
 const plan=scope.weeklyCapitalPlan({previous,current,previousCapital:100037,capital:101000,quotes:quotesOf(current)});
 assert.equal(ownRow(plan,'2033').current,125);
 const prior=directPlan(priorSignal,signal,100037,100037,quotesOf(signal));
 assert.equal(ownRow(prior,'2033').current,96);
 assert.equal(ownRow(prior,'2033').change,29);
 assert.equal(ownRow(prior,'2033').target,125);
});

test('published capital basis is required to be finite and positive when supplied',()=>{
 for(const invalid of [0,-1,Infinity,NaN]){const previous=clone(signal);previous.capital_basis=invalid;
  assert.throws(()=>directPlan(previous,nextWeek(signal),100037,101000,quotesOf(signal)),/本金基準/);
 }
});
