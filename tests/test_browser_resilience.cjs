const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../update_data.py'),'utf8').replaceAll('\r\n','\n');
function functionSource(name){const start=source.indexOf('function '+name+'('),end=source.indexOf('\n}\n',start);assert.ok(start>=0&&end>start);return source.slice(start,end+2);}
function elements(){const values={};return {values,$:id=>(values[id]??={textContent:''})};}
test('static full-period labels do not require a chart library',()=>{
 const e=elements(),scope={...e,DATA:{perf:{cagr:.4,mdd:-.3,final_nav:100,start_capital:100,nav:[['2026-10-16',100]],completed_through:'2026-10-16'}},P:String,M:String,F:String};
 vm.runInNewContext(functionSource('renderCharts')+';renderCharts();',scope);assert.equal(e.values['p-cagr'].textContent,'0.4');assert.equal(e.values['p-mdd'].textContent,'-0.3');assert.equal(e.values['p-nav'].textContent,'100');assert.equal(e.values['chart-nav'],undefined);
});
test('static labels never invoke the independently managed interactive chart renderer',()=>{
 const e=elements(),scope={...e,DATA:{perf:{}},P:String,M:String,F:String,echarts:{init(){throw Error('injected renderer failure');}}};
 assert.doesNotThrow(()=>vm.runInNewContext(functionSource('renderCharts')+';renderCharts();',scope));assert.equal(e.values['chart-dd'],undefined);assert.equal(e.values['p-period'].textContent,'— ～ —');
});
test('blocked storage reads writes and resets keep the current calculation usable',()=>{
 const e=elements(),blocked=()=>{throw Error('storage denied');};const scope={...e,localStorage:{getItem:blocked,setItem:blocked,removeItem:blocked}};
 vm.runInNewContext(functionSource('capitalStorage')+";capitalStorage('getItem');capitalStorage('setItem','150000');capitalStorage('removeItem');",scope);assert.match(e.values['cap-storage-note'].textContent,/本次試算仍有效/);
});
test('available storage still saves and resets capital',()=>{
 let value=null;const scope={...elements(),localStorage:{getItem:()=>value,setItem:(k,v)=>value=v,removeItem:()=>value=null}};
 vm.runInNewContext(functionSource('capitalStorage')+";capitalStorage('setItem','150000');",scope);assert.equal(value,'150000');vm.runInNewContext("capitalStorage('removeItem');",scope);assert.equal(value,null);
});

test('actual holdings budget cannot fall back to a different target when the primary plan is invalid',()=>{
 const line=source.split('\n').find(line=>line.startsWith('ORDER_PLANNER_UI=initOrderPlanner('));assert.ok(line);
 let captured;const scope={initOrderPlanner:options=>(captured=options),DATA:{},M:String,USER_CAPITAL:100000,WEEKLY_CAPITAL_UI:null};vm.runInNewContext(line,scope);
 assert.throws(()=>captured.getTargets(),/兩週總資金/);scope.WEEKLY_CAPITAL_UI={getTargets:()=>null};assert.throws(()=>captured.getTargets(),/兩週總資金/);
 const target=[{code:'1101',name:'fixture',quantity:10}];scope.WEEKLY_CAPITAL_UI={getTargets:()=>target};assert.equal(captured.getTargets(),target);
});
